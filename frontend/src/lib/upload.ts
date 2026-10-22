// The backend's upload limit (backend/settings.py, max_upload_mb), checked before
// sending so nobody waits on a 40 MB upload just to be refused. The server's 413 stays
// the backstop and shows its own text, which is this same message for size (D23).
export const MAX_UPLOAD_MB = 30
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
export const TOO_LARGE_MESSAGE = `That PDF is larger than the ${MAX_UPLOAD_MB} MB limit.`

export function isTooLarge(file: File): boolean {
  return file.size > MAX_UPLOAD_BYTES
}
