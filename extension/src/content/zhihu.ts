/**
 * Zhihu content script entry point.
 * Bundled as dist/content/zhihu.js and injected into zhihu.com pages.
 */

import { startCollector } from "./kernel.js";
import { installZhihuMessageListener } from "./zhihu/task-executor.js";
import { isZhihuTaskTabLocation } from "./zhihu/task-mode.js";
import { zhihuAdapter } from "../shared/platforms/zhihu.js";
import { installNativeSaveExecutor } from "./native-save/runtime.ts";
import { saveZhihu } from "./native-save/zhihu.ts";

if (!isZhihuTaskTabLocation()) {
  startCollector(zhihuAdapter);
}
installZhihuMessageListener();
installNativeSaveExecutor("zhihu", saveZhihu);
