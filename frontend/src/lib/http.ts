/** The message FastAPI sent with an error response: a string, or a list of validation errors. */
export async function errorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const detail = (await response.json())?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const messages = detail.map((item: { msg?: string }) => item.msg ?? "").filter(Boolean);
      if (messages.length) return messages.join("; ");
    }
  } catch {
    // not a JSON body
  }
  return fallback;
}
