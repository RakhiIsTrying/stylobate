import { NextResponse, type NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/middleware";

export async function proxy(request: NextRequest) {
  const { response, user } = await updateSession(request);
  const pathname = request.nextUrl.pathname;
  const isAuthPath =
    pathname.startsWith("/sign-in") ||
    pathname.startsWith("/sign-up") ||
    pathname.startsWith("/auth/");

  if (!user && !isAuthPath && pathname !== "/") {
    return NextResponse.redirect(new URL("/sign-in", request.url));
  }
  if (user && (pathname === "/" || isAuthPath)) {
    return NextResponse.redirect(new URL("/chat", request.url));
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
