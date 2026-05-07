using System.Collections.Generic;
using System.Web.Http;
using LegacyCustomerPortalNet45.Models;
using LegacyCustomerPortalNet45.Services;

namespace LegacyCustomerPortalNet45.Controllers
{
    [RoutePrefix("api/orders")]
    public class OrdersApiController : ApiController
    {
        private readonly OrderRepository repository = new OrderRepository();

        [HttpGet]
        [Route("")]
        public IEnumerable<Order> Get()
        {
            return repository.GetRecentOrders();
        }

        [HttpGet]
        [Route("{id:int}")]
        public IHttpActionResult Get(int id)
        {
            var order = repository.GetOrder(id);
            if (order == null)
            {
                return NotFound();
            }

            return Ok(order);
        }
    }
}
