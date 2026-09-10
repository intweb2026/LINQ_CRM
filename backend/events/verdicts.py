"""
events/verdicts.py
───────────────────
Which Performance Matrix verdicts mean an edition is not being worked.

ONE DEFINITION, HERE, because three modules now ask the same question and the
answer is a property of an EVENT rather than of any of them:

  credit_control  does not chase money for an edition that is not happening
  mining_matrix   does not schedule mining capacity against one either
  performance_matrix  owns the verdict itself

The obvious alternative was for the matrix to import Credit Control's copy,
which is the wrong direction — Credit Control is the newest module and the most
likely to be changed — and a second literal in each module is how the two drift
until one page excludes a cancelled event and the other quietly does not.

DELIBERATELY A PLAIN MODULE with no Django imports, so it is safe to import
from anywhere, including a `constants` module that is itself read while models
are still loading.

TBP is here with Postponed and Cancelled on purpose. It means the decision has
not been taken, so the edition is not something to spend a shift or a miner on
until it is; Standby, Going Ahead, Needs a push and Full Efforts Req. are all
live and all stay in.
"""

EXCLUDED_VERDICTS = frozenset({"TBP", "Postponed", "Cancelled"})
