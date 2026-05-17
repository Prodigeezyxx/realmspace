import { cn } from "@/lib/utils";
import { HTMLAttributes, ReactNode } from "react";

interface PanelProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  padded?: boolean;
  elevated?: boolean;
}

export function Panel({
  title,
  subtitle,
  action,
  padded = true,
  elevated,
  className,
  children,
  ...rest
}: PanelProps) {
  return (
    <div
      className={cn(
        elevated ? "panel-elevated" : "panel",
        "flex flex-col overflow-hidden",
        className
      )}
      {...rest}
    >
      {(title || action) && (
        <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-3 border-b border-border-hairline">
          <div className="min-w-0">
            {title && (
              <h3 className="text-[11px] uppercase tracking-[0.18em] text-text-secondary font-medium">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="text-xs text-text-muted mt-0.5 truncate">{subtitle}</p>
            )}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      <div className={cn(padded ? "p-5" : "", "flex-1 min-h-0")}>{children}</div>
    </div>
  );
}
