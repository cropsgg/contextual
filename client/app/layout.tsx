import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";

const inter = Inter({ variable: "--font-sans", subsets: ["latin"] });
const jetbrains = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Assistant",
  description: "Chat with long-term memory",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${inter.variable} ${jetbrains.variable} min-h-screen bg-surface font-sans antialiased text-ink`}
      >
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
