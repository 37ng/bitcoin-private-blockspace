"""Charts, drawn from the exported CSVs.

    python export_results.py && python visualize.py

Reads `${OUT_DIR}` and writes PNGs beside the data. No BigQuery access, so a
chart can be redrawn or restyled without re-running anything.

Four figures, each picked for the job its data has to do:

    private_share.png     trend over time, one measure with its threshold band
    value_bands.png       trend over time, the two value bands as a range
    sensitivity.png       a 3x3 grid of one magnitude -> heatmap, one hue
    pools.png             magnitude compared across names -> horizontal bars
"""

import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402

# Validated categorical slots, light surface. Assigned in fixed order, never
# cycled; sequential work uses the blue ramp below.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
             "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281",
             "#0d366b"]
MUTED = "#b9b8b3"


def style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK_2,
        "text.color": INK,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "font.size": 10,
        "axes.titlesize": 13,
        "axes.titleweight": "normal",
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "axes.axisbelow": True,
        "figure.dpi": 140,
    })


def recede(ax):
    """Grid and axes are context; the data is the subject."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(length=0)


def save(fig, out_dir, name):
    path = os.path.join(out_dir, name)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  {path}")


def private_share(monthly, out_dir):
    """One measure over time; the 0.3-0.7 spread is drawn as its own band."""
    fig, ax = plt.subplots(figsize=(10, 4.6))
    x = monthly["block_month"]
    ax.fill_between(x, monthly["private_vbytes_share_30"] * 100,
                    monthly["private_vbytes_share_70"] * 100,
                    color=SERIES[0], alpha=0.16, linewidth=0,
                    label="0.3 to 0.7 sensitivity")
    ax.plot(x, monthly["private_vbytes_share_50"] * 100,
            color=SERIES[0], linewidth=2, label="0.5 sensitivity")
    recede(ax)
    ax.set_title("Block space sold below the public price, share of full blocks")
    ax.set_ylabel("% of vbytes in full blocks")
    ax.set_xlabel("")
    ax.legend(frameon=False, loc="upper left")

    # Direct-label the last point rather than every point.
    if len(monthly):
        last = monthly.iloc[-1]
        value = last["private_vbytes_share_50"] * 100
        if value == value:
            ax.annotate(f"{value:.2f}%", (last["block_month"], value),
                        textcoords="offset points", xytext=(6, 0),
                        color=INK, fontsize=10, va="center")
    save(fig, out_dir, "private_share.png")


def value_bands(monthly, out_dir):
    """Two bounds on one quantity: draw the range, not two rival lines."""
    fig, ax = plt.subplots(figsize=(10, 4.6))
    x = monthly["block_month"]
    ax.fill_between(x, monthly["lower_band_btc_50"], monthly["upper_band_btc_50"],
                    color=SERIES[1], alpha=0.16, linewidth=0,
                    label="between the bands")
    ax.plot(x, monthly["upper_band_btc_50"], color=SERIES[1], linewidth=2,
            label="upper band: block median rate x vbytes")
    ax.plot(x, monthly["lower_band_btc_50"], color=SERIES[1], linewidth=2,
            linestyle=(0, (4, 2)),
            label="lower band: (floor - effective) x vbytes")
    recede(ax)
    ax.set_title("What the flagged space was worth, per month")
    ax.set_ylabel("BTC")
    ax.set_xlabel("")
    ax.legend(frameon=False, loc="upper left")
    save(fig, out_dir, "value_bands.png")


def sensitivity(grid, out_dir):
    """A 3x3 grid of one magnitude: heatmap, one hue, more is darker."""
    table = grid.pivot(index="sensitivity", columns="full_weight",
                       values="flagged_vbytes").sort_index()
    values = table.values / 1e9
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "blue_ramp", BLUE_RAMP)
    image = ax.imshow(values, cmap=cmap, aspect="auto")
    ax.set_xticks(range(len(table.columns)),
                  [f"{int(c):,}" for c in table.columns])
    ax.set_yticks(range(len(table.index)), [f"{i:g}" for i in table.index])
    ax.set_xlabel("full-block weight threshold (WU)")
    ax.set_ylabel("discount sensitivity")
    ax.set_title("Flagged space across the threshold grid (GvB)")
    ax.grid(False)
    recede(ax)

    # Every cell is labelled: nine numbers is a table with a colour cue.
    ceiling = values.max() if values.size else 0
    for row in range(values.shape[0]):
        for col in range(values.shape[1]):
            value = values[row, col]
            dark_cell = ceiling and value > ceiling * 0.6
            ax.text(col, row, f"{value:,.1f}", ha="center", va="center",
                    color=SURFACE if dark_cell else INK, fontsize=11)
    fig.colorbar(image, ax=ax, label="GvB").outline.set_visible(False)
    save(fig, out_dir, "sensitivity.png")


def pools(pool_summary, out_dir, top=12):
    """Magnitude across names: horizontal bars, one hue, sorted."""
    grouped = (pool_summary.groupby("pool_name")[
        ["flagged_vbytes_50", "vbytes", "blocks"]].sum())
    grouped["share"] = grouped["flagged_vbytes_50"] / grouped["vbytes"] * 100
    grouped = grouped[grouped["vbytes"] > 0].sort_values("share").tail(top)
    if grouped.empty:
        print("  (no pool rows to plot)")
        return

    fig, ax = plt.subplots(figsize=(8.4, max(3.5, 0.42 * len(grouped) + 1.4)))
    bars = ax.barh(grouped.index, grouped["share"], height=0.62,
                   color=SERIES[0])
    for bar in bars:
        bar.set_capstyle("round")
    recede(ax)
    ax.set_title("Flagged space as a share of each pool's own space")
    ax.set_xlabel("% of the pool's vbytes flagged (sensitivity 0.5)")
    ax.grid(axis="y", visible=False)
    for bar, value in zip(bars, grouped["share"]):
        ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                f"  {value:.3f}%", va="center", color=INK_2, fontsize=9)
    ax.margins(x=0.14)
    save(fig, out_dir, "pools.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=config.OUT_DIR)
    args = parser.parse_args()
    style()

    def read(name):
        path = os.path.join(args.out, f"{name}.csv")
        if not os.path.exists(path):
            raise SystemExit(f"{path} is missing; run export_results.py first")
        return pd.read_csv(path)

    monthly = read("monthly_summary")
    monthly["block_month"] = pd.to_datetime(monthly["block_month"])
    print(f"drawing into {args.out}/")
    private_share(monthly, args.out)
    value_bands(monthly, args.out)
    sensitivity(read("flag_a_sensitivity"), args.out)
    pools(read("pool_summary"), args.out)


if __name__ == "__main__":
    main()
