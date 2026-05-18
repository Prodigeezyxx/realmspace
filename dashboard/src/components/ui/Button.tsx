import { cn } from "@/lib/utils";
import { ButtonHTMLAttributes, forwardRef, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "inverse";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  iconAfter?: ReactNode;
  fullWidth?: boolean;
  pill?: boolean;
}

const variantStyles: Record<Variant, string> = {
  // Brand-green primary, black text — the Intellias green pill
  primary:
    "bg-accent text-text-inverse hover:bg-accent-bright shadow-[var(--glow-green)]",
  // Subtle dark outlined pill
  secondary:
    "bg-bg-raised border border-border-subtle text-text-primary hover:border-border-strong hover:bg-bg-elevated",
  ghost:
    "bg-transparent text-text-secondary hover:bg-bg-elevated hover:text-text-primary",
  danger:
    "bg-accent-red/10 border border-accent-red/30 text-accent-red hover:bg-accent-red/15",
  // White-on-black for special moments
  inverse:
    "bg-bg-inverse text-text-inverse hover:bg-white/90 shadow-[var(--shadow-sm)]",
};

const sizeStyles: Record<Size, string> = {
  sm: "h-9 px-4 text-xs gap-1.5",
  md: "h-11 px-5 text-sm gap-2",
  lg: "h-14 px-7 text-[15px] gap-2.5",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = "secondary",
      size = "md",
      icon,
      iconAfter,
      fullWidth,
      pill = true,
      className,
      children,
      ...rest
    },
    ref
  ) => (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center font-medium transition-all duration-150 select-none disabled:opacity-40 disabled:cursor-not-allowed",
        pill ? "rounded-full" : "rounded-xl",
        variantStyles[variant],
        sizeStyles[size],
        fullWidth && "w-full",
        className
      )}
      {...rest}
    >
      {icon && <span className="shrink-0">{icon}</span>}
      {children}
      {iconAfter && <span className="shrink-0">{iconAfter}</span>}
    </button>
  )
);

Button.displayName = "Button";
