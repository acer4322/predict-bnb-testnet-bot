"use client";

/**
 * Emergency-disabled.
 *
 * The previous implementation mutated React-owned <select> options while a
 * document-wide MutationObserver watched childList changes. Reassigning an
 * option's textContent retriggered the observer indefinitely and could starve
 * the dashboard main thread. Observer choices must be rendered by page.tsx,
 * never injected into the DOM after render.
 */
export default function GenericObserverSlotUnlock() {
  return null;
}
