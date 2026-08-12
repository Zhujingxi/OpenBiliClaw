import {
  parseExtensionMessage,
  type ExtensionMessage,
} from "../shared/messages";

interface RuntimePort {
  onMessage: {
    addListener(
      listener: (value: unknown) => ExtensionMessage | undefined,
    ): void;
  };
}
interface ExtensionGlobal {
  runtime?: RuntimePort;
}
const extensionRuntime = (globalThis as { chrome?: ExtensionGlobal }).chrome
  ?.runtime;
extensionRuntime?.onMessage.addListener((value: unknown) =>
  parseExtensionMessage(value),
);
