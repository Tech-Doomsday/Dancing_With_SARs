// @types/react-dom is not a dependency of this project; the Data tab only needs createPortal (timeline dock).
declare module "react-dom" {
  import type { ReactNode, ReactPortal } from "react";
  export function createPortal(children: ReactNode, container: Element | DocumentFragment, key?: string | null): ReactPortal;
}
