using System;
using System.Collections.Generic;
using System.Configuration;
using System.Linq;
using LegacyCustomerPortalNet45.Models;

namespace LegacyCustomerPortalNet45.Services
{
    public class OrderRepository
    {
        private static readonly IList<Customer> Customers = new List<Customer>
        {
            new Customer { CustomerId = 1, Name = "Northwind Traders", Email = "ops@northwind.example", Segment = "Enterprise", CreatedOn = DateTime.Today.AddYears(-4) },
            new Customer { CustomerId = 2, Name = "Contoso Retail", Email = "orders@contoso.example", Segment = "Retail", CreatedOn = DateTime.Today.AddYears(-2) },
            new Customer { CustomerId = 3, Name = "Fabrikam Health", Email = "supply@fabrikam.example", Segment = "Healthcare", CreatedOn = DateTime.Today.AddMonths(-16) }
        };

        private static readonly IList<Order> Orders = new List<Order>
        {
            new Order { OrderId = 101, CustomerId = 1, OrderNumber = "SO-1001", OrderDate = DateTime.Today.AddDays(-8), Status = "Open", TotalAmount = 14500m },
            new Order { OrderId = 102, CustomerId = 2, OrderNumber = "SO-1002", OrderDate = DateTime.Today.AddDays(-6), Status = "Shipped", TotalAmount = 8200m },
            new Order { OrderId = 103, CustomerId = 1, OrderNumber = "SO-1003", OrderDate = DateTime.Today.AddDays(-2), Status = "Open", TotalAmount = 5100m },
            new Order { OrderId = 104, CustomerId = 3, OrderNumber = "SO-1004", OrderDate = DateTime.Today.AddDays(-1), Status = "Pending Review", TotalAmount = 22300m }
        };

        public string GetPortalName()
        {
            return ConfigurationManager.AppSettings["PortalName"] ?? "Legacy Customer Portal";
        }

        public IList<Customer> GetCustomers()
        {
            return Customers.OrderBy(c => c.Name).ToList();
        }

        public IList<Order> GetRecentOrders()
        {
            return Orders.OrderByDescending(o => o.OrderDate).ToList();
        }

        public OrderDashboardViewModel GetDashboard()
        {
            var recentOrders = GetRecentOrders();
            return new OrderDashboardViewModel
            {
                PortalName = GetPortalName(),
                Customers = GetCustomers(),
                RecentOrders = recentOrders,
                TotalRevenue = recentOrders.Sum(o => o.TotalAmount),
                OpenOrders = recentOrders.Count(o => o.Status == "Open" || o.Status == "Pending Review")
            };
        }

        public Order GetOrder(int id)
        {
            return Orders.FirstOrDefault(o => o.OrderId == id);
        }
    }
}
