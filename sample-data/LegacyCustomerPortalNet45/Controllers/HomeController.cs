using System.Web.Mvc;
using LegacyCustomerPortalNet45.Services;

namespace LegacyCustomerPortalNet45.Controllers
{
    public class HomeController : Controller
    {
        private readonly OrderRepository repository = new OrderRepository();

        public ActionResult Index()
        {
            var model = repository.GetDashboard();
            return View(model);
        }
    }
}
