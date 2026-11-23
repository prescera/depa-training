"""State war-room (index patients) TDP: standardise and tokenise the raw export into its dataset directory."""

from pathlib import Path

import pandas as pd
from common import cli, standardise

args = cli(__doc__, "index", "index/poc_data_statewarroom_data.csv")
df = standardise(
    pd.read_csv(args.input),
    renames={
        "icmrnumber": "pk_icmrno",
        "pcnumber": "pk_mobno",
        "srfnumber": "ref_srfno",
        "labcode": "ref_labid",
    },
    keep=["pk_icmrno", "pk_mobno", "ref_srfno", "ref_labid"],
    prefix="index",
    identifiers=["pk_icmrno", "ref_srfno", "pk_mobno", "ref_labid"],
)
df = df.drop(columns=["index_pname", "index_paddress"])
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
df.to_csv(out / "dp_index_standardised_anon.csv", index=False)
print(f"wrote {len(df)} rows to {out}")
