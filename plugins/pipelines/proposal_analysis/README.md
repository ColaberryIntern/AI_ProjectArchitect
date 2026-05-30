# Proposal Analysis Pipeline

A built-in pipeline shipped with the Colaberry AI Operations Platform that demonstrates how to compose existing capabilities into a multi-step workflow.

## What it does

1. Accepts a raw proposal/RFP body (text) and optional client name.
2. Runs `summarize_proposal` to produce a one-page executive brief.
3. Surfaces the brief as the pipeline's `executive_brief` output.

## When to use it

- Inbound RFP triage — drop the proposal text in and get a leadership-ready brief.
- As the seed for a longer pipeline (add risk-scoring and response-drafting steps as those capabilities come online).

## How to extend it

Add new step blocks under `steps` in `manifest.json`. Each step:
- has a unique `step_id`
- points at an existing capability (`capability_id`)
- declares `depends_on` so the engine knows ordering
- maps its `input_bindings` from `$pipeline.<input>` or `$step.<other_step>.<output_field>`

The platform's pipeline_engine validates the manifest against `config/schemas/ops/pipeline_manifest.schema.json` on every load.
