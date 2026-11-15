# Windows budget artifact persistence

Budget snapshots use a closed, uniquely named temporary file in the destination directory, followed by atomic replacement. On Windows, a reader that does not allow delete sharing can briefly block replacement with access-denied or sharing/lock errors (WinError 5, 32, or 33).

For those replacement errors only, the writer makes at most six attempts with delays of 10, 20, 40, 80 and 160 milliseconds. It rechecks the remaining run time before waiting and before each retry. The operation retries the filesystem write, never the model call, grading assessment, or accounting mutation. Each retry has its own temporary file; the last complete destination remains intact until replacement succeeds.

The budget snapshot records cumulative `persistence.replace_retries` and `persistence.last_retry` (operation, destination, Windows error and attempt number). A successful snapshot includes prior retry metadata. Unrecoverable errors raise `BudgetPersistenceError` with the operation, destination and operating-system error, preserving the cause and stopping dispatch. A locked temporary candidate may remain for diagnosis if cleanup is also denied.

The regression suite covers transient and permanent errors, unrelated errors, deadline exhaustion, dispatch prevention, and a real Windows reader handle without delete sharing. A separate native timed-lock probe verifies recovery after lock release with unchanged accounting and a valid prior snapshot throughout the lock.

The benchmark finalizer uses the same atomic writer. A terminal finalization failure marks the outcome and provenance failed, records the error, and keeps the previous budget snapshot intact. Model and tool dispatch recheck the deadline after persistence, so a slow successful write cannot authorize work after expiry. Completed model-call settlement can still save accounting after the deadline.
