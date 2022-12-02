import matplotlib

# matplotlib.use("TkAgg")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats
from xas.file_io import load_binned_df_from_file, save_binned_df_as_file
import os


# def filenames_from_dir(path, base="", ext=""):
#     filenames = []
#     if type(base) == str:
#         (_, _, all_filenames) = next(os.walk(path))
#         for f in all_filenames:
#             if f.startswith(base) and f.endswith(ext) and ("merge" not in f):
#                 filenames.append(os.path.join(path, f))
#     elif type(base) == list:
#         for f in base:
#             filenames.append(os.path.join(path, f))
#     else:
#         print("Invalid file type. Return None")
#         return None
#     return np.sort(filenames)


GAIN_DICT = {
    "i0": ["ch1_amp_gain"],
    "it": ["ch2_amp_gain"],
    "ir": ["ch3_amp_gain"],
    "iff": ["ch4_amp_gain"],
}


def degain(
    df: pd.DataFrame,
    md: dict,
    df_keys=["i0", "it", "ir", "iff"],
    md_keys=["ch1_amp_gain", "ch2_amp_gain", "ch3_amp_gain", "ch4_amp_gain"],
):
    df_mV = df.copy(deep=True)
    for k, md_k in zip(df_keys, md_keys):
        df_mV[k] = df[k] * 10 ** md[md_k] * 1000
    return df_mV


def check_saturation(df: pd.DataFrame, keys=["i0", "it", "ir", "iff"], threshold=3200):
    sat_dict = dict()
    for k in keys:
        if np.any(np.abs(df[k]) > threshold):
            sat_dict[k] = False
        else:
            sat_dict[k] = True
    return sat_dict


def check_amplitude(df: pd.DataFrame, keys=["i0", "it", "ir", "iff"], threshold=20):
    low_amp_dict = dict()
    for k in keys:
        if np.all(np.abs(df[k]) < threshold):
            low_amp_dict[k] = False
        else:
            low_amp_dict[k] = True
    return low_amp_dict


def check_mu_values(df: pd.DataFrame):
    mut = -np.log(df["it"] / df["i0"])
    mur = -np.log(df["ir"] / df["i0"])
    muf = df["iff"] / df["i0"]
    valid_values = {
        "mut": np.all(np.isfinite(mut)),
        "mur": np.all(np.isfinite(mur)),
        "muf": np.all(np.isfinite(muf)),
    }
    return valid_values


def check_scan(df: pd.DataFrame, md: dict):
    df_mV = degain(df, md)
    unsaturated_currents = check_saturation(df_mV)
    good_amp_currents = check_amplitude(df_mV)
    valid_mu = check_mu_values(df_mV)
    mu_good = {mu: False for mu in ["mut", "muf", "mur"]}  # default all False
    if good_amp_currents["i0"] and unsaturated_currents["i0"]:
        if unsaturated_currents["it"]:
            mu_good["mut"] = valid_mu["mut"]
        if unsaturated_currents["iff"]:
            mu_good["muf"] = valid_mu["muf"]
    if (
        good_amp_currents["it"]
        and unsaturated_currents["it"]
        and unsaturated_currents["ir"]
    ):
        mu_good["mur"] = valid_mu["mur"]
    return mu_good


def check_scan_from_file(filename, md):
    df, _ = load_binned_df_from_file(filename)
    return check_scan(df, md)


def check_for_outliers(all_data, trim_fraction=0.2, threshold=25):
    # all_data = np.array([df["mut"] for df in dfs])
    n_pts = all_data.shape[1]
    trim_data = stats.trimboth(all_data, trim_fraction, axis=0)
    trim_mean = np.mean(trim_data, axis=0)
    trim_std = np.std(trim_data, axis=0)
    deviation_from_mean = np.sum(((all_data - trim_mean) / trim_std)**2, axis=1) / n_pts
    return deviation_from_mean, deviation_from_mean > threshold


def standardize_energy_grid(dfs, energy_key="energy"):
    energy_master = dfs[0][energy_key]
    dfs_out = [dfs[0]]
    for df in dfs[1:]:
        _df = {energy_key: energy_master}
        for column in df.columns:
            if column != energy_key:
                _df[column] = np.interp(energy_master, df[energy_key], df[column])
        dfs_out.append(pd.DataFrame(_df))
    return dfs_out


def prenormalize_data(data):
    n_curves, n_pts = data.shape
    data_out = np.zeros(data.shape)
    data_out[0, :] = data[0, :]
    for i in range(1, n_curves):
        basis = np.vstack((np.ones(n_pts), data[i, :])).T
        c, _, _, _ = np.linalg.lstsq(basis, data[0, :])
        data_out[i, :] = basis @ c
    return data_out

def average_scangroup(dfs, columns=["mut", "muf", "mur"], energy_key="energy"):
    dfs = standardize_energy_grid(dfs)
    avg_data = {energy_key: dfs[0][energy_key]}
    outliers_dict = {}
    dev_from_mean_dict = {}
    for col in columns:
        all_data = np.array([df[col] for df in dfs])
        all_data_prenorm = prenormalize_data(all_data)
        plt.plot(dfs[0][energy_key], all_data.T, 'k-', alpha=0.25)
        plt.plot(dfs[0][energy_key], all_data_prenorm.T, 'k-', alpha=1)
        # check for outliers should be done on data where n_curves >= 5
        dev_from_mean, outliers = check_for_outliers(all_data)
        avg_data[col] = np.mean(all_data[~outliers], axis=0)
        outliers_dict[col] = outliers
        dev_from_mean_dict[col] = dev_from_mean
    return pd.DataFrame(avg_data), outliers_dict, dev_from_mean_dict

def average_scangroup_from_files(filenames):
    dfs = []
    for filename in filenames:
        df, _ = load_binned_df_from_file(filename)
        df_mu = {'energy': df['energy'],
                 'mut': -np.log(df['it'] / df['i0']),
                 'muf': df['iff'] / df['i0'],
                 'mur': -np.log(df['ir'] / df['it'])}
        dfs.append(pd.DataFrame(df_mu))
    return average_scangroup(dfs)


def test():
    PATH = "/home/charles/Desktop/search_and_merge"
    df_data = pd.read_json(f"{PATH}/test_data.json")  # use uids for keys
    df_metadata = pd.read_json(f"{PATH}/test_metadata.json")  # use uids for keys
    test_df = pd.DataFrame(df_data.iloc[:, 200][0])
    test_md = dict(df_metadata.iloc[:, 200])

    test_df_mV = degain(test_df, test_md)
    print(test_df_mV, "\n")
    print(check_saturation(test_df_mV))
    print(check_amplitude(test_df_mV))
    print(check_scan(test_df, test_md))

    plt.plot(test_df["energy"], -np.log(test_df["it"] / test_df["i0"]))
    plt.plot(test_df["energy"], -np.log(test_df["ir"] / test_df["i0"]))
    plt.plot(test_df["energy"], test_df["iff"] / test_df["i0"])

    plt.show()


if __name__ == "__main__":
    test()


def average_scans(path, base, ext=""):
    filenames = filenames_from_dir(path, base=base, ext=ext)
    header_av = "# merge\n"
    path_av = os.path.join(path, base + " merged" + ext)
    # define energy grid and read the data to merge
    n = len(filenames)
    energy_lo = np.zeros(n)
    energy_hi = np.zeros(n)
    df_list = []
    for i, f in enumerate(filenames):
        df, header = load_binned_df_from_file(f)
        df_list.append(df)
        energy_lo[i] = df.iloc[:, 0].min()
        energy_hi[i] = df.iloc[:, 0].max()
        _, new_line = os.path.split(f)
        header_av += "# " + new_line + "\n"

    energy_lo = energy_lo.max()
    energy_hi = energy_hi.min()
    E = np.array(df_list[0].iloc[:, 0])
    E = E[(E >= energy_lo) & (E <= energy_hi)]

    # create new df
    idx = pd.Index(np.arange(E.size))
    columns = df_list[0].columns
    df_av = pd.DataFrame(index=idx, columns=columns)
    df_av.iloc[:, 0] = E
    df_av.iloc[:, 1:] = 0

    # average
    for each_df in df_list:
        data = np.array(each_df.iloc[:, 1:])
        n_cols = data[0, :].size
        E_cur = np.array(each_df.iloc[:, 0])
        data_int = np.array([np.interp(E, E_cur, data[:, i]) for i in range(n_cols)]).T
        df_av.iloc[:, 1:] += data_int
    df_av.iloc[:, 1:] /= n

    columns_str = "  ".join(columns)
    fmt = "%12.6f " + (" ".join(["%12.6e" for i in range(len(columns) - 1)]))
    np.savetxt(
        path_av,
        df_av.values,
        delimiter=" ",
        fmt=fmt,
        header=f"# {columns_str}",
        comments=header_av,
    )
    # dfgd
    # save_binned_df_as_file(path_av, df_av, header_av)

    # plt.figure(1)
    # plt.clf()
    # for each_df in df_list:
    #     plt.plot(each_df['energy'], each_df['iff'])
    # plt.plot(df_av['energy'], df_av['iff'], 'r-')
    # dfgef


# def test_interpolation(fpath):


# def read_offsets_from_folder(folder):
#     files = filenames_from_dir(folder,base='', ext='.dat')
#     gains = np.array([3, 4, 5, 6, 7])
#     data_dict = {'apb_ave_ch1_mean' : [[], [], [], [], []],
#                  'apb_ave_ch2_mean' : [[], [], [], [], []],
#                  'apb_ave_ch3_mean' : [[], [], [], [], []],
#                  'apb_ave_ch4_mean' : [[], [], [], [], []],
#                  'apb_ave_ch5_mean' : [[], [], [], [], []],
#                  'apb_ave_ch6_mean' : [[], [], [], [], []],
#                  'apb_ave_ch7_mean' : [[], [], [], [], []],
#                  'apb_ave_ch8_mean' : [[], [], [], [], []]}
#
#     for file in files:
#         df = pd.read_csv(file)
#         this_gain = int(file[-5])
#         idx_gain = np.where(this_gain == gains)[0][0]
#         for key in data_dict.keys():
#             offset_value = df[key].values[0]
#             # print(key, idx_gain)
#             data_dict[key][idx_gain].append(offset_value)
#
#     return gains, data_dict
#
#
#
# ggg = dd['apb_ave_ch3_mean'][1]
# plt.figure(1)
# plt.close()
# plt.plot(ggg, 'k.')
# plt.plot([0, len(ggg)], [np.median(ggg), np.median(ggg)], 'r-')
#
# out_dict = {}
# for key in dd.keys():
#     bla = {}
#     for i, gain in enumerate(gains):
#         bla[int(gain)] = float(np.median(dd[key][i]))
#     out_dict[key] = bla
