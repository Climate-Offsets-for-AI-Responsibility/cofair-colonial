/**
 * Three-position provider filter shared by /pricing and /tokens.
 *
 * Default → isolate one → add more isolates → hide active isolate → restore.
 */

export function soloProviders(modes) {
  const out = [];
  for (const [providerId, mode] of modes) {
    if (mode === "solo") out.push(providerId);
  }
  return out;
}

export function providerVisible(modes, providerId) {
  const solos = soloProviders(modes);
  if (solos.length > 0) return solos.includes(providerId);
  return modes.get(providerId) !== "hidden";
}

export function cycleProviderMode(modes, providerId) {
  const solos = soloProviders(modes);
  if (solos.length > 0) {
    if (solos.includes(providerId)) {
      for (const pid of solos) modes.delete(pid);
      modes.set(providerId, "hidden");
      return;
    }
    if (modes.get(providerId) === "hidden") {
      modes.delete(providerId);
      return;
    }
    modes.set(providerId, "solo");
    return;
  }
  if (modes.get(providerId) === "hidden") {
    modes.delete(providerId);
    return;
  }
  modes.clear();
  modes.set(providerId, "solo");
}

export function providerChipAriaLabel(providerLabel, modes, providerId) {
  const solos = soloProviders(modes);
  const mode = modes.get(providerId) || "";
  if (mode === "solo") return `${providerLabel}: isolated — click to hide`;
  if (mode === "hidden") return `${providerLabel}: hidden — click to restore`;
  if (solos.length) {
    return `${providerLabel}: hidden while other providers are isolated — click to add`;
  }
  return `${providerLabel}: shown — click to isolate`;
}
