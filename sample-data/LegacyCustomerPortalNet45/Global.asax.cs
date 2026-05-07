using System.Web.Http;
using System.Web.Mvc;
using System.Web.Routing;
using LegacyCustomerPortalNet45.App_Start;

namespace LegacyCustomerPortalNet45
{
    public class MvcApplication : System.Web.HttpApplication
    {
        protected void Application_Start()
        {
            AreaRegistration.RegisterAllAreas();
            GlobalConfiguration.Configure(WebApiConfig.Register);
            FilterConfig.RegisterGlobalFilters(GlobalFilters.Filters);
            RouteConfig.RegisterRoutes(RouteTable.Routes);
            BundleConfig.RegisterBundles();
        }
    }
}
