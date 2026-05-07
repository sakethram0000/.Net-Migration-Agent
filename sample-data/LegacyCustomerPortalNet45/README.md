# Legacy Customer Portal (.NET Framework 4.5)

Classic ASP.NET MVC 5 + Web API sample used to test migration to modern .NET.

## What it contains

- MVC frontend: `HomeController`, Razor views, CSS, JavaScript
- Web API backend: `OrdersApiController`
- In-memory repository/service layer
- `packages.config`
- `Global.asax`
- `Web.config`
- Old-style .NET Framework 4.5 `.csproj`

## Run legacy project

1. Open `LegacyCustomerPortalNet45.sln` in Visual Studio with .NET Framework 4.5 targeting pack.
2. Restore NuGet packages.
3. Run with IIS Express.
4. Open `/` for the dashboard.
5. Open `/api/orders` for the backend API.
