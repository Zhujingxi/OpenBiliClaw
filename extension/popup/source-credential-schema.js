function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function credentialFieldDefinitions(schema) {
  if (!isRecord(schema) || !isRecord(schema.properties)) return [];
  const required = new Set(Array.isArray(schema.required) ? schema.required : []);
  return Object.entries(schema.properties).flatMap(([name, definition]) => {
    if (!isRecord(definition) || definition.type !== "string") return [];
    return [{
      name,
      label: typeof definition.title === "string" && definition.title.trim()
        ? definition.title.trim()
        : name.replaceAll("_", " "),
      required: required.has(name),
      secret: definition.writeOnly === true || /(cookie|password|secret|token|key)/i.test(name),
    }];
  });
}

export function credentialsFromValues(definitions, values) {
  return Object.fromEntries(definitions.flatMap((definition) => {
    const value = String(values[definition.name] ?? "").trim();
    if (!value) {
      if (definition.required) throw new Error(`${definition.label} cannot be empty`);
      return [];
    }
    return [[definition.name, value]];
  }));
}
