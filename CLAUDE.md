# Git

Always include `[skip ci]` in every git commit message.

Do not add Claude/AI attribution (e.g. `Co-Authored-By: Claude ...`) to commit messages.

# Environment

If `cuga-flo start` is not recognized, remind the user to run `source .venv/bin/activate` first.

# Running a Kogito app

Apps using `workflow_engine: {type: kogito}` need their Quarkus service built and running
before CUGA-FLO starts. Three steps, substituting the app directory name under
`applications/`:

```bash
# 1. Build the Kogito service from the app dir (one-off, or after editing the BPMN)
./scripts/build_kogito_app.sh <app-name>

# 2. Start it — leave this running in its own terminal
build/kogito/<app-name>/run.sh

# 3. In another terminal, start CUGA-FLO
source .venv/bin/activate
cuga-flo start <app-name>
```

Then open http://127.0.0.1:8001 and give it an applicant.

Only step 1 needs repeating after a BPMN change — the yaml and policies are read live.
`loan_approval_kogito` is the worked example. See
`docs/README-KOGITO.md` for the full integration.

A Tomcat-styled 404 means the Kogito service is not up and the request reached Flowable on
8080 instead.

# Running a SimPN app

Apps using `workflow_engine: {type: simpn}` need their standalone Simulation Runtime
Service built and running before CUGA-FLO starts. Unlike Kogito, this service isn't a
third-party engine — it's built from this repo's own code (`src/cuga_flo/adapters/simpn/`)
into its own isolated venv, kept separate from CUGA FLO's main venv because `simpn` pulls
in a full GUI/plotting stack (PyQt6, pygame, matplotlib) that has no business there.
Three steps, substituting the app directory name under `applications/`:

```bash
# 1. Build the service from the app dir (one-off, or after editing the BPMN/config)
./scripts/build_simpn_app.sh <app-name>

# 2. Start it — leave this running in its own terminal
build/simpn/<app-name>/run.sh

# 3. In another terminal, start CUGA-FLO
source .venv/bin/activate
cuga-flo start <app-name>
```

Then open http://127.0.0.1:8001 and give it an applicant.

Step 1 needs repeating after any change to the BPMN model, the app config, or
`simpn_parameters.yaml` — the service reads only the staged copy under
`build/simpn/<app-name>/`, which step 1 (re-)creates. `loan_approval_simpn` is the
worked example.

To capture a unified execution trace for `cuga-flo evaluate`, set `CUGA_FLO_TRACE_PATH`
before step 3; see `docs/README-SIMPN.md`.

To run a frozen scenario across the G0-G3 autonomy ladder instead of a single manual
session, skip all three steps above — `cuga-flo run-scenario` manages the service's
build/start/stop itself:

```bash
source .venv/bin/activate
cuga-flo run-scenario <app-name> applications/<app-name>/scenarios/<scenario>.yaml \
    --configs G0,G1,G2,G3 --repetitions 3
```

See `docs/README-SIMPN.md`'s "Scenarios and the scenario runner" section.
