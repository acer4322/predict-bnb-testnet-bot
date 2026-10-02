"use client";

/**
 * Emergency-disabled with the generic Observer DOM injector.
 *
 * The accounting view will be restored as a native child of the Observer
 * panel so it can reuse the page's existing state poll instead of creating an
 * additional independent /api/state polling loop.
 */
export default function ObserverPaperAccountingStatus() {
  return null;
}
