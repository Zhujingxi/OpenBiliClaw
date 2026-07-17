/**
 * Reddit content script entry point.
 */

import { startCollector } from "./kernel.js";
import { installRedditMessageListener } from "./reddit/task-executor.ts";
import { isRedditTaskTabLocation } from "./reddit/task-mode.ts";
import { redditAdapter } from "../shared/platforms/reddit.js";

if (!isRedditTaskTabLocation()) {
  startCollector(redditAdapter);
}
installRedditMessageListener();
