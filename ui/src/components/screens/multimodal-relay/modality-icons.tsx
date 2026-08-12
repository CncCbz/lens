"use client";

import {
  AudioLines,
  FileText,
  Image,
  Type,
  Video,
  type LucideIcon,
} from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const MODALITY_META: Record<string, { label: string; icon: LucideIcon }> = {
  text: { label: "Text", icon: Type },
  image: { label: "Image", icon: Image },
  video: { label: "Video", icon: Video },
  pdf: { label: "PDF", icon: FileText },
  audio: { label: "Audio", icon: AudioLines },
};

export function ModalityIcons({
  modalities,
  className,
}: {
  modalities: string[];
  className?: string;
}) {
  const items = Array.from(new Set(modalities))
    .map((modality) => MODALITY_META[modality])
    .filter(Boolean);
  if (items.length === 0) {
    return null;
  }
  return (
    <div
      className={cn("flex items-center gap-1 text-muted-foreground", className)}
      aria-label={items.map((item) => item.label).join(", ")}
    >
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <Tooltip key={item.label}>
            <TooltipTrigger asChild>
              <span className="flex size-5 items-center justify-center">
                <Icon className="size-3.5" />
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom" align="center">
              {item.label}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}
