"use client";

import { useCallback } from "react";

import { Diagram } from "./diagram";

type SvgBlockProps = {
  code: string;
};

export function SvgBlock({ code }: SvgBlockProps) {
  // The fence already holds the SVG markup; nothing to compile.
  const render = useCallback(async (source: string) => source, []);
  return <Diagram source={code} render={render} className="my-[1em]" />;
}
