import { cn } from "@/lib/utils";
import { ButtonHTMLAttributes, forwardRef, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  iconAfter?: ReactNode;
  fullWidth?: boolean;
}

const variantStyles: Record<Variant, string> = {
  primary:
    "bg-accent-blue text-white hover:bg-accent-blue-bright shadow-[0_8px_24px_-12px_rgba(62,131,247,0.7)]",
  secondary:
    "bg-bg-elevated border border-border-subtle text-text-primary hover:border-border-strong hover:bg-[#1f1f24]",
  ghost: "bg-transparent text-text-secondary hover:bg-bg-elevated hover:text-text-primary",
  danger:
    "bg-accent-red/15 border border-accent-red/40 text-accent-red hover:bg-accent-red/25",
};

const sizeStyles: Record<Size, string> = {
  sm: "h-8 px-3 text-xs gap-1.5 rounded-md",
  md: "h-10 px-4 text-sm gap-2 rounded-lg",
  lg: "h-12 px-5 text-sm gap-2 rounded-xl",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = "secondary",
      size = "md",
      icon,
      iconAfter,
      fullWidth,
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
