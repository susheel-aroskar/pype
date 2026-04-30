# Plan for making ClientRegistry in the registries.py more efficient and performant.

`ServiceRegistry` creates few (10s - 100s) long lived, stable queues - one for each backend service. They need not cleaned up once they are created.

In contrast `ClientRegistry`,
- Can contain large (10,000 - 100,000) queues - one for each client
- Has a high churn, clients connect and disconnet all the time
- Reaper has to clean up abandoned client session (queues) constantly.

Because of this `ClientRegistry` implementation should pay special attention to runtime performance of lookups as well as inserts and deletes to get best possible overall performance. Here's a plan for better implementation of `ClientRegistry`

- self._entries: in  `ClientRegistry` is a list[ClientEntry | int] instead dict[int, ClientEntry]. 
- _entries is essentially a linked list implemented inside an array by storing either
    - actual ClientEntry keyed by client_id (int) as an index in the array OR
    - int index of a next free cell - list element that does not have any client entry stored in it. This is the free list as  linked list part.
    -First element of the _entries list - _entries[0] - acts as the head of a free list linked list. Free list keeps track of "empty" cells in the _entries list where new ClientEntries may be stored. if _entries[0] == -1, the free linked list is empty, there are no empty cells in _entries.
- _entries is = [-1]  at the time of initialize. i.e. free list is empty

- Let's say first client logs in. The free list head - i.e. _entries[0] - is -1 which means there is no place or cell empty in the free list. 
    - So we get current length of the _entries (1) and use it as new client_id (=1) 
    - Since there is no empty cell in _entries, we just append the ClientEntry to the _entries list. Now _entries is [-1, ClientEntry(1)]
    - Notice how ClientEntry's client_id matches the index of the element in _entries where it is stored. This allows us to look the ClientEntry up efficiently by using the client_id as an index into _entries[] in O(1) time.

- Let's say 3 more clients join after that. we will generate client_ids 2, 3, 4 and the _entries will be [-1, ClientEntry(1), ClientEntry(2), ClientEntry(3), ClientEntry(4)] following same logic in the point above.

- Now let's client with client_id == 2 logs off. This is how we handle the update of `ClientRegistry`:
    - we store whatever is in _entries[0] - -1 in this case - in the cell that was occupied by the logging off client id - 2 in this case.
    - Then we store the logging off client's client_id (2) in the cell [0].
    - So now the _entries is [2, ClientEntry(1), -1, ClientEntry(3), ClientEntry(4)] 
    - So the head of the free list is pointing to cell #2 which is indeed empty, and cell# 2 is pointing to -1 which means there are no more free cells after that.
    - free list: 2 -> -1

- Now let's client with client_id == 4 logs off. Following the same logic as above:
    - we store whatever is in _entries[0] - 2 in this case - in the cell that was occupied by the logging off client id - 4 in this case.
    - Then we store the logging off client's client_id (4) in the cell [0].
    - So now the _entries is [4, ClientEntry(1), -1, ClientEntry(3), 2] 
    - So the head of the free list is pointing to cell #4, which in turn is pointing to 2, which is the last free cell, indicated by the sentinel end value of -1. That is free list rooted in _entries[0] looks like this 4 -> 2 -> -1. Each int in this sequence is an index of a cell that is empty, with cell containing -1 is the tail or end of the free list.

- Now let's say a new client logs in. We look at the head of the free list to see what client_id should we generate. The free list head - _entrie[0] - is no longer -1. It is 4. So we give the client the client_id of 4 and adjust the free list as follows:
    - ClientEntry gets stored at the index pointed by what's in _entries[0] - the free list head, in this case 4 (same as the generated client_id). This cell is guaranteed to be empty since we checked that _entries[0] != -1
    - Whatever was int at the cell# 4 will be copied to _entries[0]. In this case 2.
    - The _entries[] is now [2, ClientEntry(1), -1, ClientEntry(3), ClientEntry(4)] 
    - free list is 2 -> -1
    - Notice the invariant. Each ClientEntry is still at the index give by its client_id.
    - The ClientEntry(4) now is different from the old ClientEntry(4) for the client that logged off but this is identifiable by client_secret that'd be different between them.

- Now let's say one more client logs in:
    - _entries[0] == 2 so client_id is 2 for the new logged in client.
    - _entries[0] = entries[2]
    - _entries[2] = ClientEntry(2)
    - entries = [-1, ClientEntry(1), ClientEntry(2), ClientEntry(3), ClientEntry(4)] 
    - free list -> -1 , i.e. full
    - the invariant ClientEntry.request_id == index inti _entries[] still holds

- Finally let's say one more client logs off
    - _entries[0] == -1, no more free cells in _entries, so new client_id = len(_entries) = 5
    - append to the list (since there is no free cell in the _entries)
    - Now entries = [-1, ClientEntry(1), ClientEntry(2), ClientEntry(3), ClientEntry(4), ClientEntry(5)] 
    - free list -> -1 , i.e. full, still full
    - the invariant ClientEntry.request_id == index inti _entries[] still holds

Now the reaper that checks the client_registry periodically and evicts inactive clients does not have to take a snapshot of keys. It can directly operate on the _entries[] array inside the ClientRegistry without any fear of corruption as long as it knows to ignore any ints it encouters in the _entries[]. These are essentially next-> pointers of our free list

    


- Now lets