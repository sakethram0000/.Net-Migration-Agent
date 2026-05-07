using System;

namespace LegacyCustomerPortalNet45.Models
{
    public class Customer
    {
        public int CustomerId { get; set; }
        public string Name { get; set; }
        public string Email { get; set; }
        public string Segment { get; set; }
        public DateTime CreatedOn { get; set; }
    }
}
