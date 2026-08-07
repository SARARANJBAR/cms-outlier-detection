"""The one screen.

Pick a specialty and a procedure or drug, see the peer group's distribution with one
provider marked on it, filter by state, and read what CMS censored out of the picture.

Run it with:

    uv run streamlit run src/cms_outliers/app/main.py

Every percentile shown here was computed at build time. The screen reads the 3.3 MB
`peer_stats` table for the distribution and a filtered slice of the fact table for the
provider — no percentile is computed while anyone waits. See docs/optimization notes in
`notebooks/03_optimization.ipynb`.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from cms_outliers.app import queries as q

BREAKPOINTS = [10, 25, 50, 75, 90, 95, 99]

SUPPRESSION = {
    "part_b": """
**What is missing from this picture.** CMS removes any provider-procedure row covering
fewer than 11 patients. It does not blank the value — it deletes the row. So the providers
who do this procedure rarely are not shown as missing data; they are simply absent, and
nothing in the file says so. The distribution below has had its bottom cut off, and a
percentile is a rank against the providers who remained.
""",
    "part_d": """
**What is missing from this picture.** CMS blanks the patient count on 55% of Part D rows —
systematically the small prescriber-drug pairs. Claims and cost are never blanked, which is
why those are the only measures scored here. Any per-patient figure would silently describe
the larger half of the distribution while appearing to describe all of it.
""",
}


@st.cache_resource
def get_connection():
    return q.connect()


@st.cache_data
def cached(fn_name: str, *args, **kwargs):
    """Cache a query by name. Streamlit hashes the arguments; the connection is separate."""
    return getattr(q, fn_name)(get_connection(), *args, **kwargs)


def distribution_chart(breaks: pd.DataFrame, marked: pd.Series | None, measure_label: str):
    """The peer group's percentile curve, with one provider marked on it."""
    curve = pd.DataFrame(
        {
            "percentile": BREAKPOINTS,
            "value": [float(breaks[f"p{p}"].iloc[0]) for p in BREAKPOINTS],
        }
    )
    base = alt.Chart(curve).encode(
        x=alt.X("percentile:Q", title="Percentile of peer group", scale=alt.Scale(domain=[0, 105])),
        y=alt.Y("value:Q", title=measure_label, scale=alt.Scale(type="symlog")),
    )
    layers = [
        base.mark_area(opacity=0.15, interpolate="monotone"),
        base.mark_line(interpolate="monotone", point=True),
    ]

    if marked is not None:
        point = pd.DataFrame(
            {
                "percentile": [float(marked["percentile"])],
                "value": [float(marked["value"])],
                "label": [f"{marked['provider']} — {marked['percentile']:.1f}th"],
            }
        )
        marker = alt.Chart(point)
        layers += [
            marker.mark_rule(strokeDash=[4, 4], color="crimson").encode(x="percentile:Q"),
            marker.mark_point(size=180, color="crimson", filled=True).encode(
                x="percentile:Q", y="value:Q", tooltip=["label"]
            ),
            marker.mark_text(dy=-16, color="crimson", fontWeight="bold").encode(
                x="percentile:Q", y="value:Q", text="label"
            ),
        ]

    return alt.layer(*layers).properties(height=340)


def main() -> None:
    st.set_page_config(page_title="CMS provider outliers", layout="wide")
    st.title("Medicare provider outliers, 2023")
    st.caption(
        "Where a provider sits against peers doing the same thing — same specialty, "
        "same procedure or drug. Public CMS data."
    )

    controls, screen = st.columns([1, 3], gap="large")

    with controls:
        dataset = st.radio(
            "Dataset",
            list(q.DATASETS),
            format_func=lambda d: "Part B — procedures" if d == "part_b" else "Part D — drugs",
        )
        specialty = st.selectbox("Specialty", cached("specialties", dataset))

        code_options = cached("codes", dataset, specialty)
        if code_options.empty:
            st.warning("No peer group for this specialty reaches 30 providers.")
            return
        code_labels = {
            row.code: f"{row.code} — {(row.label or '?')[:40]} ({row.n_providers:,} peers)"
            for row in code_options.itertuples()
        }
        code = st.selectbox(
            q.DATASETS[dataset][2], list(code_labels), format_func=lambda c: code_labels[c]
        )
        pos = code_options.loc[code_options.code == code, "pos"].iloc[0]
        group = q.PeerGroup(dataset, specialty, code, pos if dataset == "part_b" else None)

        measures = q.MEASURES[dataset]
        measure = st.selectbox("Measure", list(measures), format_func=lambda m: measures[m][2])

        all_states = ["All states", *cached("states", group)]
        state_choice = st.selectbox("State", all_states)
        state = None if state_choice == "All states" else state_choice

    breaks = cached("distribution", group, measure)
    if breaks.empty:
        with screen:
            st.warning("This peer group has no published statistics for that measure.")
        return

    top = cached("candidates", group, measure, state)
    measure_label = measures[measure][2]

    with controls:
        marked = None
        if not top.empty:
            picked = st.selectbox(
                "Mark a provider",
                top.npi.tolist(),
                format_func=lambda npi: _provider_label(top, npi),
            )
            marked = top.loc[top.npi == picked].iloc[0]

    with screen:
        n = int(breaks.n_providers.iloc[0])
        cols = st.columns(4)
        cols[0].metric("Peers in group", f"{n:,}")
        cols[1].metric("Median", f"{breaks.p50.iloc[0]:,.1f}")
        cols[2].metric("90th percentile", f"{breaks.p90.iloc[0]:,.1f}")
        cols[3].metric("99th percentile", f"{breaks.p99.iloc[0]:,.1f}")

        if marked is not None:
            ratio = marked["value"] / breaks.p50.iloc[0] if breaks.p50.iloc[0] else float("nan")
            st.markdown(
                f"**{marked['provider']}, {marked['first_name']}** "
                f"({marked['city']}, {marked['state']}) is at the "
                f"**{marked['percentile']:.1f}th percentile** — "
                f"{measure_label.lower()} of **{marked['value']:,.1f}**, "
                f"**{ratio:,.1f}x** the peer median."
            )

        st.altair_chart(distribution_chart(breaks, marked, measure_label), use_container_width=True)

        st.subheader("Top of the peer group")
        st.caption(
            "Ranks are precomputed, so this is a filtered read rather than a percentile "
            "computed on demand."
        )
        display = top.head(25).copy()
        display["provider"] = (
            display["provider"].str.title() + ", " + display["first_name"].str.title()
        )
        display["percentile"] = display["percentile"].round(1)
        display["value"] = display["value"].round(1)
        display = display[["npi", "provider", "city", "state", "value", "percentile"]]
        st.dataframe(
            display.rename(
                columns={
                    "npi": "NPI",
                    "provider": "Provider",
                    "city": "City",
                    "state": "State",
                    "value": measure_label,
                    "percentile": "Percentile",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

        st.info(SUPPRESSION[dataset])
        st.caption(
            "Being an outlier is not evidence of wrongdoing. High utilization can reflect "
            "a referral practice, a sicker panel, or a sub-specialty this peer key does not "
            "separate. Peer groups below 30 providers are not ranked at all."
        )


def _provider_label(top: pd.DataFrame, npi: str) -> str:
    row = top.loc[top.npi == npi].iloc[0]
    return f"{row['provider']}, {row['first_name']} ({row['state']}) — {row['percentile']:.1f}th"


main()
