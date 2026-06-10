export function buildPatchPayload({assignmentId, stage, username, baseVersion, changes, openedAt}) {
  return {
    assignment_id: assignmentId,
    stage,
    username,
    base_version: baseVersion || null,
    changes,
    client_timing: {opened_at: openedAt, saved_at: Date.now() / 1000}
  };
}
