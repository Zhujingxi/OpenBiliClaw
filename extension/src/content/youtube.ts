/**
 * YouTube content script entry point.
 * Bundled as dist/content/youtube.js and injected into youtube.com pages.
 */
import { installYtMessageListener } from "./yt/task-executor.js";
import { startCollector } from "./kernel.js";
import { youtubeAdapter } from "../shared/platforms/youtube.js";
import { installNativeSaveExecutor } from "./native-save/runtime.ts";
import { saveYouTube } from "./native-save/youtube.ts";

startCollector(youtubeAdapter);
installYtMessageListener();
installNativeSaveExecutor("youtube", saveYouTube);
