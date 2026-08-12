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

const MODALITY_ORDER = ["text", "image", "video", "pdf", "audio"] as const;

const MODALITY_META: Record<string, { label: string; icon: LucideIcon }> = {
  text: { label: "Text", icon: Type },
  image: { label: "Image", icon: Image },
  video: { label: "Video", icon: Video },
  pdf: { label: "PDF", icon: FileText },
  audio: { label: "Audio", icon: AudioLines },
};

export function ModalityToggleRow({
  value,
  onChange,
  disabled,
  className,
}: {
  value: Record<string, boolean>;
  onChange?: (next: Record<string, boolean>) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-1.5", className)}>
      {MODALITY_ORDER.map((modality) => {
        const meta = MODALITY_META[modality];
        if (!meta) return null;
        const Icon = meta.icon;
        const active = Boolean(value[modality]);
        return (
          <Tooltip key={modality}>
            <TooltipTrigger asChild>
              <button
                type="button"
                disabled={disabled}
                aria-pressed={active}
                aria-label={meta.label}
                onClick={() =>
                  onChange?.({
                    ...value,
                    [modality]: !active,
                  })
                }
                className={cn(
                  "flex size-8 items-center justify-center rounded-lg border transition-colors",
                  active
                    ? "border-primary bg-primary/15 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground",
                  disabled && "cursor-not-allowed opacity-50",
                )}
              >
                <Icon className="size-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom" align="center">
              {meta.label}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}
