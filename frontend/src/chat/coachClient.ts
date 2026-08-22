import {
  copyThread,
  createCoachFetch,
  createThread,
  deleteThread,
  getThread,
  getThreadState,
  getUploadStatus,
  postFeedback,
  postUpload,
  searchThreads,
  streamRun,
  type CoachFetch,
  type CoachStreamClient,
} from "./coachApi";
import type { CoachChatDeps } from "./useCoachChat";
import { getSupabase } from "@/lib/supabase";
import { createCoachClient, supabaseAccessToken } from "@/lib/langgraph";

/**
 * Production wiring: fetchers bound to the member bearer (refresh-aware via
 * the Supabase session) + the SDK stream client. Tests never touch this —
 * they inject their own deps bundle.
 */

let cachedDeps: CoachChatDeps | null = null;

export function createBrowserDeps(): CoachChatDeps {
  if (cachedDeps !== null) return cachedDeps;
  const supabase = getSupabase();
  const fetcher: CoachFetch = createCoachFetch(() => supabaseAccessToken(supabase));
  const client: CoachStreamClient = createCoachClient(() => supabaseAccessToken(supabase));
  cachedDeps = {
    api: {
      createThread: () => createThread(fetcher),
      searchThreads: (options) => searchThreads(fetcher, options),
      getThread: (threadId) => getThread(fetcher, threadId),
      deleteThread: (threadId) => deleteThread(fetcher, threadId),
      copyThread: (threadId) => copyThread(fetcher, threadId),
      getThreadState: (threadId) => getThreadState(fetcher, threadId),
      postUpload: (upload) => postUpload(fetcher, upload),
      getUploadStatus: (uploadId) => getUploadStatus(fetcher, uploadId),
      postFeedback: (feedback) => postFeedback(fetcher, feedback),
    },
    stream: {
      streamRun: (threadId, payload) => streamRun(client, threadId, payload),
    },
    auth: { signOut: () => supabase.auth.signOut() },
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    newUploadId: () => crypto.randomUUID(),
    poll: {
      erase: { pollMs: 1500, maxPolls: 40 },
      upload: { pollMs: 1200, maxPolls: 50 },
    },
  };
  return cachedDeps;
}
