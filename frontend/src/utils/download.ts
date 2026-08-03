/**
 * Trigger a browser download for an in-memory Blob.
 *
 * Uses an invisible <a download> element + programmatic click, then revokes
 * the object URL so the blob can be GC'd. Safe for binary (e.g. PDF) and
 * text blobs. Filename with non-ASCII is handled by the browser.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  // Some browsers require the anchor to be in the document to trigger click.
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Defer revoke slightly so the click navigation has a chance to start.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
