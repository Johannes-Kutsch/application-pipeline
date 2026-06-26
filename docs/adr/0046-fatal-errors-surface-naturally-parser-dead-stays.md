# Fatal errors surface naturally; parser-dead keeps failure report

Fatal pipeline errors (classify failure, judge failure, unexpected SDK exceptions) previously wrote a markdown **Failure Report** to `.runtime-data/failures/` and exited with code 1 but no terminal output — the operator had no immediate signal and had to know to check the failures directory. Replaced with natural exception propagation: the unhandled exception prints a full traceback to stderr and exits non-zero, which is visible in the terminal for `run` and in cron mail for `cron`.

Parser-dead events are the one exception. A dead parser is non-fatal — the run continues with the remaining parsers and may still produce a **Daily Results File**. Surfacing it naturally would require making it fatal, which is too aggressive. Parser-dead keeps its **Failure Report** (traceback only; log-tail removed) plus a stderr notification pointing to the report path so the operator is notified immediately without needing to check the directory.

The acknowledgment-by-deletion pattern for fatal errors is retired: with a traceback on stderr, there is nothing to acknowledge.
