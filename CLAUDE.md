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
