export async function api(path, body, signal) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json'},
    signal,
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try { message = JSON.parse(text).error || text; } catch { /* Plain HTTP errors also have readable bodies. */ }
    throw new Error(message);
  }
  return response.json();
}

export const identityPath = name => `/api/identities/${encodeURIComponent(name)}`;
