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

# Part B splits every procedure by where it was performed, and the two are genuinely
# different peer groups: for chest X-rays in radiology the 99th percentile is $7,168 in a
# facility against $18,288 in an office. 1,997 (specialty, code) pairs have both, so the
# picker has to offer them separately rather than collapse them by code.
PLACE_OF_SERVICE = {"F": "facility", "O": "office"}

SUPPRESSION = {
    "part_b": """
**What is missing from this picture.** CMS removes any provider-procedure row covering
fewer than 11 patients. It does not blank the value — it deletes the row. So the providers
who do this procedure rarely are not shown as missing data; they are simply absent, and
nothing in the file says so. The distribution here has had its bottom cut off, and a
percentile is a rank against the providers who remained.
""",
    "part_d": """
**What is missing from this picture.** CMS blanks the patient count on 55% of Part D rows —
systematically the small prescriber-drug pairs. Claims and cost are never blanked, which is
why those are the only measures scored here. Any per-patient figure would silently describe
the larger half of the distribution while appearing to describe all of it.
""",
}

LIMITS = (
    "Being an outlier is not evidence of wrongdoing. High utilization can reflect a referral "
    "practice, a sicker panel, or a sub-specialty this peer key does not separate. Peer groups "
    "below 30 providers are not ranked at all. State filters the provider list, not the "
    "comparison — state is deliberately not part of the peer key, because adding it would "
    "leave 19.35% of Part B rows with too few peers to score."
)

NO_FACTS = """
**Provider-level drill-down is not available here.** The distributions above come from
`peer_stats`, a 3.3 MB table that ships in the repository. Placing an individual provider on
one of them needs the fact tables — 941 MB of Parquet, a build artifact rather than a
repository file — so the hosted version stops at the distribution.

Clone the repo and run the build (see the README) and the same screen gains the provider
picker and the ranked table. Publishing those tables as a downloadable asset is deliberately
deferred work, not an oversight: the measurements in notebook 03 show remote reads are
round-trip-bound, so it needs a latency budget rather than an upload.
"""


# Where a visitor lands. Alphabetical order opens on "Addiction Medicine", which is a real
# peer group but a dull first impression; chest X-rays in radiology are a large group with a
# dramatic tail, so the screen demonstrates itself before anyone touches a control. Falls back
# to the first option if a future year does not contain these.
LANDING = {
    "part_b": {
        "specialty": "Diagnostic Radiology",
        "code": "71046",
        "measure": "total_medicare_payment",
    },
    "part_d": {
        "specialty": "Family Practice",
        "code": "Atorvastatin Calcium",
        "measure": "cost_per_claim",
    },
}


def _index(options, wanted) -> int:
    """Index of `wanted` in `options`, or 0 if it is not there."""
    options = list(options)
    return options.index(wanted) if wanted in options else 0


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
        landing = LANDING[dataset]
        all_specialties = cached("specialties", dataset)
        specialty = st.selectbox(
            "Specialty", all_specialties, index=_index(all_specialties, landing["specialty"])
        )

        code_options = cached("codes", dataset, specialty)
        if code_options.empty:
            st.warning("No peer group for this specialty reaches 30 providers.")
            return

        # Keyed on the row, not the code: one code can be two peer groups.
        def _code_label(i: int) -> str:
            row = code_options.loc[i]
            where = f" · {PLACE_OF_SERVICE.get(row.pos, row.pos)}" if row.pos else ""
            return f"{row.code} — {(row.label or '?')[:38]}{where} ({row.n_providers:,} peers)"

        landing_rows = code_options.index[code_options.code == landing["code"]]
        code_row = st.selectbox(
            q.DATASETS[dataset][2],
            code_options.index.tolist(),
            index=int(landing_rows[0]) if len(landing_rows) else 0,
            format_func=_code_label,
        )
        chosen = code_options.loc[code_row]
        group = q.PeerGroup(
            dataset, specialty, chosen.code, chosen.pos if dataset == "part_b" else None
        )

        measures = q.MEASURES[dataset]
        measure = st.selectbox(
            "Measure",
            list(measures),
            index=_index(measures, landing["measure"]),
            format_func=lambda m: measures[m][2],
        )

        facts = q.has_facts(get_connection())
        state = None
        if facts:
            all_states = ["All states", *cached("states", group)]
            state_choice = st.selectbox("State", all_states)
            state = None if state_choice == "All states" else state_choice

    breaks = cached("distribution", group, measure)
    if breaks.empty:
        with screen:
            st.warning("This peer group has no published statistics for that measure.")
        return

    top = cached("candidates", group, measure, state) if facts else pd.DataFrame()
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

        if not facts:
            st.warning(NO_FACTS)

        st.subheader("Top of the peer group")
        if not facts:
            st.caption(
                "Not available in the hosted version — see the note above. Running locally "
                "after a build, this lists the providers at the top of the group with their "
                "precomputed percentile."
            )
        else:
            st.caption(
                "Ranks are precomputed, so this is a filtered read rather than a percentile "
                "computed on demand."
            )
        if top.empty:
            st.info(SUPPRESSION[dataset])
            st.caption(LIMITS)
            return

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
        st.caption(LIMITS)


def _provider_label(top: pd.DataFrame, npi: str) -> str:
    row = top.loc[top.npi == npi].iloc[0]
    return f"{row['provider']}, {row['first_name']} ({row['state']}) — {row['percentile']:.1f}th"


main()
