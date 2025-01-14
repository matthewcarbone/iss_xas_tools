"""
Functions for testing outlier detection estimators on XAS data
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl

# mpl.use("TkAgg")
from scipy import stats
from sklearn.covariance import EllipticEnvelope
from sklearn.neighbors import LocalOutlierFactor
from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM

from xas.analysis import standardize_energy_grid, prenormalize_data, check_scan
from xas.energy_calibration import compute_shift_between_spectra

TEST_PATH = "/home/charles/Desktop/test_data/outlier_muf.pkl"


def outlier_plot(
    x_vals, data: np.ndarray, outlier_labels: np.ndarray, ax: plt.Axes = None
):
    """Plot data with outliers labeled in red.

    Args:
        x_vals (array_like): values for x-axis of plot (usually energy)
        data (np.ndarray): data to be plotted, arranged in rows
        outlier_labels (list_like): -1 for outliers and +1 for inliers
        ax (plt.Axes, optional): Axis to plot on. If `None` will use current plot axis.

    Returns:
        list of mpl.lines.Line2D
    """
    if ax is None:
        ax = plt.gca()
    line_colors = ["r" if ol < 0 else "k" for ol in outlier_labels]
    ax.set_prop_cycle(color=line_colors)
    lines = ax.plot(x_vals, data.T)
    return lines


def add_toy_outlier(data: np.ndarray) -> np.ndarray:
    """
    Add toy "outlier" to row-based data array.
    Toy outlier is made by adding scaled Gaussian noise
    to the data average.
    """
    avg_data = np.mean(data, axis=0)
    noise = np.random.randn(avg_data.size) / 1000
    toy_outlier = avg_data + noise
    new_data = np.vstack((data, toy_outlier))
    return new_data


def load_muf_data(path) -> tuple[np.ndarray, np.ndarray]:
    """
    Load muf data from pickle file.
    Return tuple of energy array and muf spectra (in array rows).
    """
    test_dfs: pd.Series = pd.read_pickle(path)
    test_dfs = test_dfs.to_list()
    muf_data = np.array([scan["muf"] for scan in test_dfs])
    energy = test_dfs[0]["energy"]
    return energy, muf_data


def trim_by_row(data: np.ndarray, trim_fraction: float) -> np.ndarray:
    """
    Trim rows from array based on their relative distance to the
    median row data.
    Distance for each row is defined by the average of the sqaure of the deviation
    from the median for each point.
    """
    med = np.median(data, axis=0)
    dist_from_med = (data - med) ** 2
    avg_row_dist = np.mean(dist_from_med, axis=1)
    return data[avg_row_dist <= np.quantile(avg_row_dist, 1 - trim_fraction)]


def fit_trimmed(estimator, data: np.ndarray, trim_fraction: float) -> None:
    """Shorthand function to fit estimator with trimmed data"""
    trim_data = stats.trimboth(data, trim_fraction / 2)
    estimator.fit(trim_data)


def fit_row_trimmed(estimator, data: np.ndarray, trim_fraction: float) -> None:
    trim_data = trim_by_row(data, trim_fraction)
    estimator.fit(trim_data)


def calc_mod_chisq(data: np.ndarray) -> np.ndarray:
    mad = np.median(np.abs(data - np.median(data, axis=0)).ravel()) / 0.67449
    ksi = (data - np.median(data, axis=0)) / mad
    n_pts = data.shape[1]
    mod_chisq = 1 / n_pts * np.sum(ksi**2, axis=1)
    return mod_chisq


def modified_chisq_rejection(data: np.ndarray, threshold=30) -> np.ndarray:
    mod_chisq = calc_mod_chisq(data)
    results = mod_chisq > threshold
    return np.array([-1 if res else 1 for res in results])


def MCS_into_LOF(data: np.ndarray, threshold=30) -> np.ndarray:
    """First use modified chi-sq (MCS) rejection to label potential outliers.
    Then fit LocalOutlierFactor (LOF) to data with MCS outliers removed.
    Finally predict outliers on original data with fitted LOF estimator.

    Args:
        data (np.ndarray): Data on which to check for outliers.
        Should be arranged in rows.
        threshold (int, optional): Threshold for MCS rejection. Defaults to 30.

    Returns:
        np.ndarray: Final results from LOF prediction.
        -1 for anomalies/outliers and +1 for inliers.
    """
    mod_chisq_results = modified_chisq_rejection(data, threshold=threshold)
    reduced_data = data[mod_chisq_results > 0]
    if reduced_data.shape[0] < 2:
        return np.ones(data.shape[0])
    est = LocalOutlierFactor(novelty=True)
    est.fit(reduced_data)
    return est.predict(data)


def outlier_rejection(
    scangroup: list[pd.DataFrame],
    uids: list[str],
    channels=("mut", "muf", "mur"),
    master_idx=0,
    energy_key="energy",
    plot_diagnostics=False,
) -> tuple[dict, dict]:
    """Perform outlier rejection using trimmed sklearn LocalOutlierFactor,
    modified chi-square (MCS), and a combined approach.

    Args:
        scangroup (list[pd.DataFrame]): List of dataframes with XAS scan data.
        uids (list[str]): Uid string for each scan.
        channels (iterable): Channels to iterate over. Defaults to ("mut", "muf", "mur").
        master_idx (int, optional): Master index for energy grid alignment. Defaults to 0.
        energy_key (str, optional): Defaults to "energy".
        plot_diagnostics (bool, optional): Plot results of outlier rejection for
        each channel. Defaults to False.

    Returns:
        average_mus: Dictionary with inlier averaged data for each channel and for each
        method of outlier rejection.
        results: Dictionary with scan uids separated into inliers and outliers for each
        channel and each method of outlier rejection.
    """
    # type cast to np.ndarray for masking later
    uids = np.array(uids)

    scangroup = standardize_energy_grid(
        scangroup, master_idx=master_idx, energy_key=energy_key
    )
    energy = scangroup[master_idx][energy_key]

    results = {ch: None for ch in channels}
    average_mus = {ch: None for ch in channels}
    average_mus[energy_key] = energy

    if plot_diagnostics:
        fig, axs = plt.subplots(len(channels), 3)
        axs[0][0].set_title("trimmed LocalOutlierFactor")
        axs[0][1].set_title("Modified Chi-Sq")
        axs[0][2].set_title("Combined (MCS -> LOF)")

    for i, ch in enumerate(channels):
        outlier_dict = dict(
            trimmed_lof={"inliers": None, "outliers": None},
            mod_chisq={"inliers": None, "outliers": None},
            combined={"inliers": None, "outliers": None},
        )
        average_dict = {}

        data = np.array([scan[ch] for scan in scangroup])
        data_prenorm = prenormalize_data(data, energy)

        est = LocalOutlierFactor(novelty=True)
        fit_trimmed(est, data, 0.4)
        lof_results = est.predict(data_prenorm)
        outlier_dict["trimmed_lof"]["inliers"] = uids[lof_results > 0].tolist()
        outlier_dict["trimmed_lof"]["outliers"] = uids[lof_results < 0].tolist()
        average_dict["trimmed_lof"] = lof_avg = np.mean(data[lof_results > 0], axis=0)

        mcs_results = modified_chisq_rejection(data_prenorm)
        outlier_dict["mod_chisq"]["inliers"] = uids[mcs_results > 0].tolist()
        outlier_dict["mod_chisq"]["outliers"] = uids[mcs_results < 0].tolist()
        average_dict["mod_chisq"] = mcs_avg = np.mean(data[mcs_results > 0], axis=0)

        combined_results = MCS_into_LOF(data_prenorm)
        outlier_dict["combined"]["inliers"] = uids[combined_results > 0].tolist()
        outlier_dict["combined"]["outliers"] = uids[combined_results < 0].tolist()
        average_dict["combined"] = combined_avg = np.mean(
            data[combined_results > 0], axis=0
        )

        results[ch] = outlier_dict
        average_mus[ch] = average_dict

        if plot_diagnostics:
            outlier_plot(energy, data_prenorm, lof_results, ax=axs[i][0])
            outlier_plot(energy, data_prenorm, mcs_results, ax=axs[i][1])
            outlier_plot(energy, data_prenorm, combined_results, ax=axs[i][2])
            axs[i][0].plot(energy, lof_avg, "b-")
            axs[i][1].plot(energy, mcs_avg, "b-")
            axs[i][2].plot(energy, combined_avg, "b-")

    plt.show()

    return average_mus, results


def compare_LOF_modchisq(df_path):
    chisq_scores = []
    df_uid: pd.DataFrame = pd.read_pickle(df_path)
    for sg in df_uid["scan_group"].unique():
        print(f"scan group {sg}")
        sg_df_uid = df_uid[(df_uid["scan_group"] == sg) & (df_uid["muf_good"])]
        test_dfs = sg_df_uid["data"]
        test_dfs.reset_index(drop=True, inplace=True)

        if len(test_dfs.to_list()) <= 4:
            continue

        test_dfs = standardize_energy_grid(test_dfs.to_list())

        mu_data = np.array([scan["muf"] for scan in test_dfs])
        energy = test_dfs[0]["energy"]
        mu_prenorm = prenormalize_data(mu_data, energy)

        est = LocalOutlierFactor(novelty=True)
        fit_trimmed(est, mu_prenorm, 0.4)
        LOF_labels = est.predict(mu_prenorm)
        print(f"LOF labels: {LOF_labels} \n")

        modchisq_labels = modified_chisq_rejection(mu_prenorm)
        print(f"Modified chi-sq values: {calc_mod_chisq(mu_prenorm)}")
        print(f"Modified chi-sq labels: {modchisq_labels} \n")
        chisq_scores.append(calc_mod_chisq(mu_prenorm))

        try:
            combined_labels = MCS_into_LOF(mu_prenorm)
            print(f"Combined labels: {combined_labels}")
        except Exception as e:
            print(e)
        if np.any(np.concatenate((LOF_labels, modchisq_labels, combined_labels)) < 0):
            fig, (ax1, ax2, ax3) = plt.subplots(1, 3)
            outlier_plot(energy, mu_prenorm, LOF_labels, ax=ax1)
            outlier_plot(energy, mu_prenorm, modchisq_labels, ax=ax2)
            outlier_plot(energy, mu_prenorm, combined_labels, ax=ax3)
            ax1.set_title("trimmed LocalOutlierFactor")
            ax2.set_title("Modified Chi-Sq")
            ax3.set_title("Combined (MCS -> LOF)")
            plt.show()
    return np.hstack(chisq_scores)


def test(estimator, add_outlier=False):
    test_dfs: pd.Series = pd.read_pickle(TEST_PATH)
    test_dfs = test_dfs.to_list()
    muf_data = np.array([scan["muf"] for scan in test_dfs])
    energy = test_dfs[0]["energy"]

    muf_prenorm = prenormalize_data(muf_data)

    # reduce amount of scans
    # muf_prenorm = muf_prenorm[0:5]

    if add_outlier:
        muf_prenorm = add_toy_outlier(muf_prenorm)

    if estimator is None:
        outlier_labels = np.ones(muf_prenorm.shape[0])
    else:
        outlier_labels = estimator.fit_predict(muf_prenorm)

    print(outlier_labels)
    print(estimator.decision_function(muf_prenorm))
    print(estimator.score_samples(muf_prenorm))
    ax = outlier_plot(energy, muf_prenorm, outlier_labels)

    plt.show()


if __name__ == "__main__":
    test(IsolationForest())


###
# for sg in df_uid["scan_group"].unique():
#     print(f"scan group {sg}")
#     sg_df_uid = df_uid[df_uid["scan_group"] == sg]
#     test_dfs = sg_df_uid["data"]
#     test_dfs.reset_index(drop=True, inplace=True)
#     muf_data = np.array([scan["muf"] for scan in test_dfs])
#     energy = test_dfs[0]["energy"]
#     if muf_data.shape[0] <= 1:
#         continue
#     muf_prenorm = prenormalize_data(muf_data)
#     outlier_labels = EllipticEnvelope().fit_predict(muf_prenorm)
#     print(outlier_labels)
#     if np.any(outlier_labels < 0):
#         ax = outlier_plot(energy, muf_prenorm, outlier_labels)
#         plt.show()


def plot_scangroup(sg_df, channels=("mut", "muf", "mur")):
    for i, scan in sg_df.iterrows():
        for ch in channels:
            plt.plot(scan["data"]["energy"], scan["data"][ch])
    plt.show()


def check_refs(sg_df_uid: pd.DataFrame) -> None:
    sg_df_uid["mur_source"] = None
    if all(sg_df_uid["mur_good"]):
        return
    if all(sg_df_uid["mur_good"] == False):
        print("Failed. No good refs in scan group.")
        return
    # bad_ref_inds = sg_df_uid.index[sg_df_uid["mur_good"]==False]
    bad_ref_df = sg_df_uid[sg_df_uid["mur_good"] == False]
    good_ref_df = sg_df_uid[sg_df_uid["mur_good"] == True]

    for i, scan_time in bad_ref_df["time"].items():
        closest_time_idx = np.abs(scan_time - good_ref_df["time"]).idxmin()
        source_mur = good_ref_df.loc[closest_time_idx, "data"]["mur"]
        sg_df_uid.loc[i, "data"]["mur"] = source_mur
        sg_df_uid.loc[i, "mur_source"] = good_ref_df.loc[closest_time_idx, "uid"]


def big_test(df_uid):
    outlier_results = []
    sg_dfs_out = []
    for sg in df_uid.scan_group.unique()[1:2]:
        sg_df_uid = df_uid[df_uid.scan_group == sg]
        outlier_results.append(
            outlier_rejection(sg_df_uid.data.tolist(), sg_df_uid.uid.tolist())
        )
        check_refs(sg_df_uid)
        sg_dfs_out.append(sg_df_uid)
    return outlier_results, sg_dfs_out
