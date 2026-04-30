# pype
Who: I’m a principal engineer implementing a long held idea in my head as a take home assignment for interview.

What: I’m going to implement “pype” - a push-pull based load balancing solution as a distributed systems primitive to replace the traditional reverse proxy based load-balancers.  It acts as a central rendezvous point + message broker. Instead of clients sending requests to a reverse proxy/load balancer that then routes these requests to backend services using some load-balancing scheme, both the clients and the backend services connect to pype instance and,
	- Clients push their API requests to the in memory queues for designated  for individual services
       - Services pull their requests from these queues and process them
	- Responses to the client flow back similarly through per client transient queues. Clients can either poll or block on their response queue when they are ready to consume their responses

Why: Reversing the request routing from push to pull for services and introducing fast, in memory queues between clients and services in both the directions makes solutions to many of the problems inherent in distributed systems easy
- Service discovery: No need for complicated service discovery mechanisms to keep track of constantly changing (because of auto-scaling etc.) dynamic service instances’ IPs etc. All the new instance of services connect to a well known pype instance(s) and crashed / shutdown service instances stop consuming requests automatically.
- load balancing: Near optimal load balancing happens organically. Each instance of a backend service instance - process or a thread inside a process - pulls new request to serve when it is done serving the old request and ready for new work. No need for the central load balancer to keep track of constantly changing state of load, latency etc. for each of the backend service instance which are constantly coming up and going down. No need for complicated power of two or least connections calculations or relying on simple but sub-optimal round-robin load balancing
- Backpressure similarly happens organically. We set max capacity for each backend service’s request queue. If it reaches its limit, new client trying to push the request in the queue is automatically blocked. The client can chose a timeout for such a push operation after which it will get a 503 - service overloaded response
- Makes auto-scaling super-easy. Length of the service queues provide ready-made, real time metric for each service to base its autoscaling actions on and new instances coming up can immediately consuming requests by talking to well known (handful) pype instance(s) without having to wait for any kind of service discovery to kick in.
- A single response queue per client across all the backend services makes it trivial to support many complex concurrent API call patterns  like multiple concurrent calls, fan-out/fan-in, first response wins etc. using a single (main calling) thread on the client side.
- Naturally supports streaming responses from server to clients
- Easy to add common features like authentication, rate-limiting etc.







