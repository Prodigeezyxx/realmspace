import { cn } from "@/lib/utils";
import { ButtonHTMLAttributes, forwardRef, ReactNode } from "react";

type Variant =
  | "primary"
  | "selected"
  | "secondary"
  | "ghost"
  | "danger"
  | "inverse";
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
  // Action orange, per brand.md §3: "primary CTAs" is the first thing on its
  // list of uses. Dark text on it rather than white — white on #FF5C00 measures
  // 3.10:1 and fails for anything at button size, while the base colour is
  // 6.35:1 on the same fill.
  primary:
    "bg-accent-action text-text-inverse hover:bg-accent-action-bright shadow-[var(--glow-action)]",
  // "This toggle is on" — which is not the same thing as "press this".
  // The twin's heatmap switch used `primary` for its active state, which was
  // invisible while primary was the same calm colour as everything else and
  // became a permanent alert the moment primary turned Action orange. brand.md
  // §3 reserves that colour for things demanding attention; a view toggle
  // showing its own state is not one.
  selected:
    "bg-accent/12 border border-accent/40 text-accent hover:bg-accent/18",
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
