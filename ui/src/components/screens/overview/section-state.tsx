import { AlertCircle } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function SectionSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("space-y-3", className)}>
      <Skeleton className="h-6 w-32" />
      <Skeleton className="h-44 w-full" />
    </div>
  );
}

export function SectionMessage({
  label,
  tone = "muted",
  className,
}: {
  label: string;
  tone?: "muted" | "error";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-36 items-center justify-center rounded-md border border-dashed px-4 py-8 text-center text-sm",
        tone === "error"
          ? "border-destructive/30 text-destructive"
          : "text-muted-foreground",
        className,
      )}
    >
      {tone === "error" && <AlertCircle className="mr-2 size-4 shrink-0" />}
      <span>{label}</span>
    </div>
  );
}

export function errorLabel(error: unknown, fallback: string) {
  if (error instanceof Error && error.message)
    return `${fallback}: ${error.message}`;
  return fallback;
}
