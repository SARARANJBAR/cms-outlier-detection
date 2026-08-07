-- One row per provider, across both datasets.
--
-- Safe as a single table because of three measured facts (notebook 02, section 8): no
-- NPI carries more than one specialty within a dataset, the specialty strings agree for
-- all 706,614 NPIs present in both, and every NPI carries exactly one entity code. No
-- crosswalk needed. The build checks reassert the first two rather than trust this note.
--
-- Part B carries the richer attribute set (address, RUCA, credentials, participation);
-- Part D has only city and state. Where a provider is in both, Part B wins and Part D
-- fills the gaps. Part-D-only prescribers therefore have null address detail, which is
-- expected, not missing data.
--
-- any_value() is safe only to the extent the attribute really is constant per NPI;
-- sql/checks/12_provider_attributes.sql counts the NPIs where it is not.

CREATE OR REPLACE VIEW dim_provider AS
WITH b AS (
    SELECT
        Rndrng_NPI                                  AS npi,
        any_value(Rndrng_Prvdr_Ent_Cd)              AS entity_type,
        any_value(Rndrng_Prvdr_Last_Org_Name)       AS last_or_org_name,
        any_value(Rndrng_Prvdr_First_Name)          AS first_name,
        any_value(Rndrng_Prvdr_MI)                  AS middle_initial,
        any_value(Rndrng_Prvdr_Crdntls)             AS credentials,
        any_value(Rndrng_Prvdr_Type)                AS specialty,
        any_value(Rndrng_Prvdr_St1)                 AS street1,
        any_value(Rndrng_Prvdr_St2)                 AS street2,
        any_value(Rndrng_Prvdr_Zip5)                AS zip5,
        any_value(Rndrng_Prvdr_City)                AS city,
        any_value(Rndrng_Prvdr_State_Abrvtn)        AS state,
        any_value(Rndrng_Prvdr_Cntry)               AS country,
        any_value(Rndrng_Prvdr_RUCA)                AS ruca,
        any_value(Rndrng_Prvdr_RUCA_Desc)           AS ruca_desc,
        any_value(Rndrng_Prvdr_Mdcr_Prtcptg_Ind)    AS medicare_participating
    FROM raw_part_b
    GROUP BY npi
),
d AS (
    SELECT
        Prscrbr_NPI                                 AS npi,
        any_value(Prscrbr_Last_Org_Name)            AS last_or_org_name,
        any_value(Prscrbr_First_Name)               AS first_name,
        any_value(Prscrbr_Type)                     AS specialty,
        any_value(Prscrbr_City)                     AS city,
        any_value(Prscrbr_State_Abrvtn)             AS state
    FROM raw_part_d
    GROUP BY npi
)
SELECT
    coalesce(b.npi, d.npi)                              AS npi,
    b.entity_type                                       AS entity_type,
    coalesce(b.last_or_org_name, d.last_or_org_name)    AS last_or_org_name,
    coalesce(b.first_name, d.first_name)                AS first_name,
    b.middle_initial                                    AS middle_initial,
    b.credentials                                       AS credentials,
    coalesce(b.specialty, d.specialty)                  AS specialty,
    b.street1                                           AS street1,
    b.street2                                           AS street2,
    b.zip5                                              AS zip5,
    coalesce(b.city, d.city)                            AS city,
    coalesce(b.state, d.state)                          AS state,
    b.country                                           AS country,
    b.ruca                                              AS ruca,
    b.ruca_desc                                         AS ruca_desc,
    b.medicare_participating                            AS medicare_participating,
    b.npi IS NOT NULL                                   AS in_part_b,
    d.npi IS NOT NULL                                   AS in_part_d
FROM b
FULL JOIN d ON b.npi = d.npi;
