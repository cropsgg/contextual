"use client";

import Link from "next/link";
import {
  ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

type DropdownMenuProps = {
  trigger: ReactNode;
  children: ReactNode;
  align?: "start" | "end";
  className?: string;
  onOpenChange?: (open: boolean) => void;
};

export function DropdownMenu({
  trigger,
  children,
  align = "end",
  className = "",
  onOpenChange,
}: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const setOpenState = useCallback(
    (next: boolean) => {
      setOpen(next);
      onOpenChange?.(next);
    },
    [onOpenChange],
  );

  useEffect(() => {
    if (!open) return;

    function onPointerDown(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpenState(false);
      }
    }

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpenState(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, setOpenState]);

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <div
        aria-expanded={open}
        aria-haspopup="menu"
        aria-controls={menuId}
        onClick={(e) => {
          e.stopPropagation();
          setOpenState(!open);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            e.stopPropagation();
            setOpenState(!open);
          }
        }}
      >
        {trigger}
      </div>
      {open ? (
        <div
          id={menuId}
          role="menu"
          className={`menu-panel absolute top-full mt-1 ${
            align === "end" ? "right-0" : "left-0"
          }`}
          onClick={() => setOpenState(false)}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}

type DropdownMenuItemProps = {
  children: ReactNode;
  onClick?: () => void;
  href?: string;
  variant?: "default" | "danger";
  className?: string;
};

export function DropdownMenuItem({
  children,
  onClick,
  href,
  variant = "default",
  className = "",
}: DropdownMenuItemProps) {
  const baseClass =
    variant === "danger" ? "menu-item-danger" : "menu-item";

  if (href) {
    return (
      <Link href={href} role="menuitem" className={`${baseClass} ${className}`}>
        {children}
      </Link>
    );
  }

  return (
    <button
      type="button"
      role="menuitem"
      className={`${baseClass} ${className}`}
      onClick={(e) => {
        e.stopPropagation();
        onClick?.();
      }}
    >
      {children}
    </button>
  );
}
