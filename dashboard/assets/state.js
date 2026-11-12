/* Stream IDs isolate reconnect/scope changes. Views never infer runtime success. */
(function (root) {
  function reduceEnvelope(state, e) {
    if (!e || e.v !== 1 || !e.stream_id) return { ...state, resync: true };
    if (e.type === "snapshot") {
      if (state.retired?.includes(e.stream_id)) return state;
      const retired = [...(state.retired || [])];
      if (state.stream_id && state.stream_id !== e.stream_id)
        retired.push(state.stream_id);
      return { ...e, retired, resync: false };
    }
    if (e.stream_id !== state.stream_id || e.scope !== state.scope)
      return state;
    if (e.seq <= state.seq) return state;
    if (e.type === "resync" || e.seq !== state.seq + 1)
      return { ...state, resync: true };
    if (e.type !== "state.updated") return { ...state, resync: true };
    const data = { ...e.data };
    for (const kind of ["agents", "steps", "missions"]) {
      const previous = new Map(
        (state.data?.[kind] || []).map((x) => [x.id, x]),
      );
      if (data[kind])
        data[kind] = data[kind].map((x) => {
          const old = previous.get(x.id);
          const rev = x.rev ?? x.revision,
            oldRev = old?.rev ?? old?.revision;
          return old &&
            rev !== undefined &&
            oldRev !== undefined &&
            rev <= oldRev
            ? old
            : x;
        });
    }
    return { ...state, ...e, data, resync: false };
  }
  root.reduceEnvelope = reduceEnvelope;
  if (typeof module !== "undefined") module.exports = { reduceEnvelope };
})(typeof window !== "undefined" ? window : globalThis);
