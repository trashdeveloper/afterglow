# Afterglow EXPECTED checksums (seed 42, deterministic Verilator)

Verified from live `runs/live-*/artifacts/verdict_{before,after}.json`.
Repro MUST print BEFORE/AFTER matching these; MISMATCH = RED.

## picosoc-uart-div (locked, model-verbatim, 92.6s)
BEFORE 27ee482b473c74db5dce8b5ab1a233084cce3055c317a1f2045faa6dbc13a1c6
AFTER  5c8f58d0f0818c97d014d2f851a6140882bd1efc96a2537047806252c0c2caf9

## picorv32-fetch-misalign (locked, bootstrap-gated, 38.7s)
BEFORE a29998cd8e81501b64b44a66e009c12b297662db5ef5369b12ef3feae608e5d5
AFTER  5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a

## uart-tx-div (locked, model-verbatim, 71.0s)
BEFORE e251d79bc7250f734efe026d9d7f036e818ad243c9428000f5cdb2c1fd8b03c6
AFTER  5c8f58d0f0818c97d014d2f851a6140882bd1efc96a2537047806252c0c2caf9

## uart-start-half (stress, model-verbatim, 51.9s)
BEFORE 7ead7d9c1ebd862283bf0991c6a01fd4f7dad63f24f2be953b7f57e1f7edd073
AFTER  5c8f58d0f0818c97d014d2f851a6140882bd1efc96a2537047806252c0c2caf9

## fetch-highword (stress, model-verbatim, 53.5s)
BEFORE 563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15
AFTER  5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a

## rdata-latch (stress, model-verbatim, 92.9s)
BEFORE c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659
AFTER  5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a

## trap-stuck (stress, model-verbatim, 75.3s)
BEFORE c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659
AFTER  5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a

## pc-incr (stress, bootstrap-gated, 170.9s)
BEFORE 563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15
AFTER  5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a
