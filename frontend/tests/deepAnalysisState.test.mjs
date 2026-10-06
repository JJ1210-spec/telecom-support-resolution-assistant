import assert from "node:assert/strict";
import test from "node:test";
import { analysisEntry, editAnalysis, runAnalysis, subscribeAnalysis } from "../src/pages/admin/deepAnalysisState.js";

test("back navigation restores the same in-flight and completed analysis", async () => {
  const entry = analysisEntry("admin:ticket-back", "");
  let releaseComplaint;
  const complaint = new Promise((resolve) => { releaseComplaint = resolve; });
  let pipelineRuns = 0;
  const started = runAnalysis(entry, () => complaint, async (text) => {
    pipelineRuns++;
    return { trace_id: "first-run", complaint: text };
  });
  const unsubscribe = subscribeAnalysis(entry, () => {});
  unsubscribe(); // leave Deep Analysis for a source detail while the pipeline runs
  assert.strictEqual(analysisEntry("admin:ticket-back", "example"), entry);
  assert.equal(entry.loading, true);

  releaseComplaint("My broadband keeps dropping");
  await started;
  assert.equal(entry.text, "My broadband keeps dropping");
  assert.equal(entry.result.trace_id, "first-run");
  assert.equal(entry.loading, false);
  assert.equal(pipelineRuns, 1);
  assert.equal(analysisEntry("another-admin:ticket-back", "").result, null);
});

test("editing cancels an old result and Analyze can deliberately run again", async () => {
  const entry = analysisEntry("admin:ticket-edit", "original complaint");
  let releaseOld;
  const oldResult = new Promise((resolve) => { releaseOld = resolve; });
  const oldRun = runAnalysis(entry, async () => entry.text, () => oldResult);
  await Promise.resolve();
  editAnalysis(entry, "updated complaint");
  releaseOld({ trace_id: "stale" });
  await oldRun;
  assert.equal(entry.result, null);
  assert.equal(entry.text, "updated complaint");

  await runAnalysis(entry, async () => entry.text, async () => ({ trace_id: "new-run" }));
  assert.equal(entry.result.trace_id, "new-run");
});
