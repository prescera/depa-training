"""ICMR (test results) TDP: standardise and tokenise the raw export into its dataset directory."""

from pathlib import Path

import pandas as pd
from common import cli, standardise

args = cli(__doc__, "icmr", "icmr/poc_data_icmr_data.csv")
df = standardise(
    pd.read_csv(args.input),
    renames={
        "icmrnumber": "pk_icmrno",
        "icmr_patient_mobilenumber": "pk_mobno",
        "srfnumber": "ref_srfno",
        "a_lab_id": "fk_icmr_labid",
        "a_sample_genetic_strain": "fk_genetic_strain",
    },
    keep=["pk_icmrno", "pk_mobno", "ref_srfno", "fk_icmr_labid", "fk_genetic_strain"],
    prefix="icmr",
    identifiers=["pk_mobno", "ref_srfno", "pk_icmrno", "fk_icmr_labid"],
)
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
df.to_csv(out / "dp_icmr_standardised_anon.csv", index=False)
print(f"wrote {len(df)} rows to {out}")
