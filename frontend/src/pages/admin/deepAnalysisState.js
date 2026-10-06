// Keep a completed or in-flight analysis while the admin navigates to its sources.
// Keys include the admin ID, so another signed-in admin never sees this browser tab's result.
const entries = new Map();

function notify(entry) {
  for (const listener of entry.listeners) listener();
}

export function analysisEntry(key, initialText) {
  if (!entries.has(key)) {
    entries.set(key, { text: initialText, result: null, error: null, loading: false,
      started: false, revision: 0, listeners: new Set() });
  }
  return entries.get(key);
}

export function subscribeAnalysis(entry, listener) {
  entry.listeners.add(listener);
  listener();
  return () => entry.listeners.delete(listener);
}

export function editAnalysis(entry, text) {
  entry.revision++;
  entry.started = true;
  entry.text = text;
  entry.result = null;
  entry.error = null;
  entry.loading = false;
  notify(entry);
}

export async function runAnalysis(entry, loadText, analyzeText) {
  const revision = ++entry.revision;
  entry.started = true;
  entry.result = null;
  entry.error = null;
  entry.loading = true;
  notify(entry);
  try {
    const text = await loadText();
    if (entry.revision !== revision) return;
    entry.text = text;
    notify(entry);
    const result = await analyzeText(text);
    if (entry.revision === revision) entry.result = result;
  } catch (error) {
    if (entry.revision === revision) entry.error = error instanceof Error ? error.message : "Analysis failed";
  } finally {
    if (entry.revision === revision) {
      entry.loading = false;
      notify(entry);
    }
  }
}
