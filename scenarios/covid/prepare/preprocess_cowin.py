"""CoWIN (vaccination) TDP: standardise and tokenise the raw export into its dataset directory."""

from pathlib import Path

import pandas as pd
from common import cli, standardise

args = cli(__doc__, "cowin", "cowin/poc_data_cowin_data.csv")
df = standardise(
    pd.read_csv(args.input),
    renames={
        "beneficiary_reference_id": "pk_beneficiary_id",
        "cowin_registered_mobile": "pk_mobno",
        "uhid": "ref_uhid",
        "id_verified": "ref_id_verified",
    },
    keep=["pk_beneficiary_id", "pk_mobno", "ref_uhid", "ref_id_verified"],
    prefix="cowin",
    identifiers=["pk_beneficiary_id", "ref_uhid", "pk_mobno", "ref_id_verified"],
)
# Free-text personal fields are not needed by any consumer and never leave the provider.
df = df.drop(columns=["cowin_beneficiary_name"])
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
df.to_csv(out / "dp_cowin_standardised_anon.csv", index=False)
print(f"wrote {len(df)} rows to {out}")
