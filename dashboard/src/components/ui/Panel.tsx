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
        <div className="flex items-start justify-between gap-3 px-5 md:px-6 pt-5 pb-4 border-b border-border-hairline">
          <div className="min-w-0">
            {title && (
              <h3 className="text-[12px] font-bold tracking-[.08em] uppercase text-text-primary truncate">{title}</h3>
            )}
            {subtitle && (
              <p className="text-xs text-text-muted mt-1 text-pretty">{subtitle}</p>
            )}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      <div className={cn(padded ? "p-5 md:p-6" : "", "flex-1 min-h-0")}>{children}</div>
    </div>
  );
}
