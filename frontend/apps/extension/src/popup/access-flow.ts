export type ArtifactKind = "cookie" | "local_storage" | "session_storage";

export interface AccessArtifact {
  kind: ArtifactKind;
  domain: string;
  name: string;
}

export interface AccessRecipe {
  domains: string[];
  artifacts: AccessArtifact[];
  warmup_url: string | null;
  target_method_id: string;
}

export interface ProviderRecipe {
  providerId: string;
  recipe: AccessRecipe;
}

interface SourceList {
  items: Array<{ provider_id: string }>;
}

export interface BrowserArtifacts {
  requestOrigins(origins: string[]): Promise<boolean>;
  open(url: string): Promise<void>;
  cookie(domain: string, name: string): Promise<string | undefined>;
  storage(
    domain: string,
    kind: "local_storage" | "session_storage",
    name: string,
  ): Promise<string | undefined>;
}

function headers(token: string, mutation = false): Record<string, string> {
  return {
    authorization: `Bearer ${token}`,
    ...(mutation
      ? {
          "content-type": "application/json",
          "x-device-id": "openbiliclaw-extension",
          "x-csrf-token": "openbiliclaw-extension",
        }
      : {}),
  };
}

function isDomain(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 253) return false;
  const labels = value.split(".");
  return (
    labels.length > 1 &&
    labels.every((label) =>
      /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label),
    ) &&
    !/^\d+(?:\.\d+){3}$/.test(value)
  );
}

function isRecipe(value: unknown): value is { recipe: AccessRecipe } {
  if (typeof value !== "object" || value === null) return false;
  const recipe = (value as { recipe?: unknown }).recipe;
  if (typeof recipe !== "object" || recipe === null) return false;
  const fields = recipe as Partial<AccessRecipe>;
  if (
    !Array.isArray(fields.domains) ||
    fields.domains.length < 1 ||
    fields.domains.length > 32 ||
    !fields.domains.every(isDomain) ||
    !Array.isArray(fields.artifacts) ||
    fields.artifacts.length < 1 ||
    fields.artifacts.length > 64 ||
    typeof fields.target_method_id !== "string" ||
    !/^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$/.test(fields.target_method_id)
  )
    return false;
  const domains = new Set(fields.domains);
  const artifactsValid = fields.artifacts.every(
    (artifact: unknown) =>
      typeof artifact === "object" &&
      artifact !== null &&
      ["cookie", "local_storage", "session_storage"].includes(
        String((artifact as Partial<AccessArtifact>).kind),
      ) &&
      domains.has(String((artifact as Partial<AccessArtifact>).domain)) &&
      typeof (artifact as Partial<AccessArtifact>).name === "string" &&
      ((artifact as Partial<AccessArtifact>).name?.length ?? 0) > 0 &&
      ((artifact as Partial<AccessArtifact>).name?.length ?? 0) <= 256,
  );
  if (!artifactsValid) return false;
  if (fields.warmup_url === null) return true;
  if (typeof fields.warmup_url !== "string") return false;
  try {
    const warmup = new URL(fields.warmup_url);
    return (
      warmup.protocol === "https:" &&
      domains.has(warmup.hostname) &&
      warmup.username === "" &&
      warmup.password === "" &&
      warmup.hash === ""
    );
  } catch {
    return false;
  }
}

function isSourceList(value: unknown): value is SourceList {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as Partial<SourceList>).items) &&
    (value as SourceList).items.every(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        typeof item.provider_id === "string" &&
        /^[a-z][a-z0-9_-]{0,63}$/.test(item.provider_id),
    )
  );
}

export async function discoverRecipes(
  origin: string,
  token: string,
  fetcher: typeof fetch = fetch,
): Promise<ProviderRecipe[]> {
  const sourcesResponse = await fetcher(`${origin}/v1/sources`, {
    headers: headers(token),
  });
  if (!sourcesResponse.ok) throw new Error("Backend connection failed");
  const sources: unknown = await sourcesResponse.json();
  if (!isSourceList(sources)) throw new Error("Invalid source response");
  const recipes = await Promise.all(
    sources.items.map(async ({ provider_id }) => {
      const response = await fetcher(
        `${origin}/v1/sources/${encodeURIComponent(provider_id)}/access-recipe`,
        { headers: headers(token) },
      );
      if (response.status === 404) return undefined;
      if (!response.ok) throw new Error("Recipe lookup failed");
      const value: unknown = await response.json();
      if (!isRecipe(value)) throw new Error("Invalid recipe response");
      return { providerId: provider_id, recipe: value.recipe };
    }),
  );
  return recipes.filter((item): item is ProviderRecipe => item !== undefined);
}

export async function openWarmup(
  recipe: AccessRecipe,
  browser: BrowserArtifacts,
): Promise<void> {
  if (recipe.warmup_url !== null) await browser.open(recipe.warmup_url);
}

export async function connectFromRecipe(
  origin: string,
  token: string,
  provider: ProviderRecipe,
  browser: BrowserArtifacts,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const origins = provider.recipe.domains.map(
    (domain) => `https://*.${domain}/*`,
  );
  if (!(await browser.requestOrigins(origins)))
    throw new Error("Site permission denied");
  const artifacts = await Promise.all(
    provider.recipe.artifacts.map(async (artifact) => {
      const value =
        artifact.kind === "cookie"
          ? await browser.cookie(artifact.domain, artifact.name)
          : await browser.storage(
              artifact.domain,
              artifact.kind,
              artifact.name,
            );
      if (value === undefined || value === "")
        throw new Error(`Required artifact unavailable: ${artifact.name}`);
      return { ...artifact, value };
    }),
  );
  const response = await fetcher(
    `${origin}/v1/sources/${encodeURIComponent(provider.providerId)}/access-material`,
    {
      method: "POST",
      headers: headers(token, true),
      body: JSON.stringify({ artifacts }),
    },
  );
  if (!response.ok) throw new Error(`Connection failed (${response.status})`);
  const result: unknown = await response.json();
  const status =
    typeof result === "object" && result !== null
      ? (result as { status?: unknown }).status
      : undefined;
  const state =
    typeof status === "object" && status !== null
      ? (status as { state?: unknown }).state
      : undefined;
  if (state !== "connected")
    throw new Error(
      `Credential verification failed${typeof state === "string" ? ` (${state})` : ""}`,
    );
}

interface ChromeApi {
  permissions: {
    request(details: { origins: string[] }): Promise<boolean>;
  };
  cookies: {
    getAll(details: {
      domain: string;
      name: string;
    }): Promise<Array<{ value: string }>>;
  };
  tabs: {
    create(details: { url: string }): Promise<unknown>;
    query(details: { url: string[] }): Promise<Array<{ id?: number }>>;
  };
  scripting: {
    executeScript(details: {
      target: { tabId: number };
      func: (kind: string, name: string) => string | null;
      args: [string, string];
    }): Promise<Array<{ result?: string | null }>>;
  };
}

function chromeApi(): ChromeApi {
  const api = (globalThis as { chrome?: ChromeApi }).chrome;
  if (api === undefined) throw new Error("Browser extension API unavailable");
  return api;
}

export const chromeArtifacts: BrowserArtifacts = {
  requestOrigins: (origins) => chromeApi().permissions.request({ origins }),
  async open(url) {
    await chromeApi().tabs.create({ url });
  },
  async cookie(domain, name) {
    const matches = await chromeApi().cookies.getAll({ domain, name });
    // ponytail: first domain/name match; add a declarative path only when a provider needs it.
    return matches[0]?.value;
  },
  async storage(domain, kind, name) {
    const tabs = await chromeApi().tabs.query({
      url: [`https://*.${domain}/*`],
    });
    const tabId = tabs[0]?.id;
    if (tabId === undefined) return undefined;
    const results = await chromeApi().scripting.executeScript({
      target: { tabId },
      func: (storageKind, key) =>
        (storageKind === "local_storage"
          ? localStorage
          : sessionStorage
        ).getItem(key),
      args: [kind, name],
    });
    return results[0]?.result ?? undefined;
  },
};
