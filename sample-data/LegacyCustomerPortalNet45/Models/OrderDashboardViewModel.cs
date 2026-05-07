using System.Collections.Generic;

namespace LegacyCustomerPortalNet45.Models
{
    public class OrderDashboardViewModel
    {
        public string PortalName { get; set; }
        public IList<Customer> Customers { get; set; }
        public IList<Order> RecentOrders { get; set; }
        public decimal TotalRevenue { get; set; }
        public int OpenOrders { get; set; }
    }
}
