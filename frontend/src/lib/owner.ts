export interface OwnerSession {
  configured: boolean;
  authenticated: boolean;
}

export async function fetchOwnerSession(): Promise<OwnerSession> {
  const response = await fetch("/api/v1/auth/session", { credentials: "same-origin" });
  if (!response.ok) throw new Error("Could not check owner session");
  return response.json() as Promise<OwnerSession>;
}

export async function loginOwner(password: string): Promise<void> {
  const response = await fetch("/api/v1/auth/login", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (response.status === 401) throw new Error("Incorrect password");
  if (response.status === 429) throw new Error("Too many attempts. Try again later.");
  if (!response.ok) throw new Error("Sign-in is unavailable");
}

export async function logoutOwner(): Promise<void> {
  const response = await fetch("/api/v1/auth/logout", {
    method: "POST", credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Could not sign out");
}
