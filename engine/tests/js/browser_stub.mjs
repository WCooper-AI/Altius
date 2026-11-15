// Just enough of a browser for the interface modules to load in node: every browser global is a
// stand-in that accepts any call, so tests can run the plain functions (no page is drawn).
const NONE = new Set(["then", "parentNode", "parentElement", "firstChild", "lastChild", "nextSibling", "previousSibling",
  "firstElementChild", "lastElementChild", "nextElementSibling", "previousElementSibling", "length"]);
const any = new Proxy(function () {}, {
  get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === Symbol.iterator ? function* () {} : NONE.has(k) ? null : any),
  apply: () => any, construct: () => any, set: () => true,
});
for (const k of ["document", "window", "location", "localStorage", "sessionStorage", "history", "navigator", "matchMedia",
  "Node", "Element", "HTMLElement", "SVGElement", "IntersectionObserver", "ResizeObserver", "MutationObserver",
  "requestAnimationFrame", "getComputedStyle", "CustomEvent", "KeyboardEvent", "Event"]) {
  Object.defineProperty(globalThis, k, { value: any, configurable: true, writable: true });
}
globalThis.fetch = () => new Promise(() => {});
